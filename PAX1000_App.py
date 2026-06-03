#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PAX1000 数据采集系统 GUI

运行模式：
  - 自动循环：按设定间隔持续截图识别，结果保存 CSV + JSONL，界面实时刷新；
              远程协议 GetPAX1000Data 返回最近一次缓存值。
  - 单次采集：点击按钮或远程协议触发，对同一帧做 ≥3 次识别取众数；
              若全部失败则返回失败提示。

远程协议（TCP JSON，与协议文档格式一致）：
  请求  {"opcode": "GetPAX1000Data", "parameter": {}}
  响应  {"IsSuccessful": true/false, "Value": {...}, "ErrorMessage": "Null"}

  支持 opcode：
    GetPAX1000Data  —— 自动模式返回缓存，单次模式触发一次采集
    CaptureOnce     —— 强制触发单次采集（不管当前模式）
    GetStatus       —— 返回运行模式、是否采集中、最近采集时间
    check           —— 心跳检测

配置文件：config.json（与本文件同目录）
"""

import os
import sys
import json
import csv
import time
import base64
import threading
import socket
import logging
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime
from collections import Counter
from typing import Optional

from PAX1000_Reader import PAX1000Reader, PAX1000Reading
from tesseract_setup import install as tess_install

# ─── 配置 ────────────────────────────────────────────────────────────────────

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

DEFAULT_CONFIG: dict = {
    "tesseract_cmd":        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    "roi_config": {
        "x_ratio": 0.0045,
        "y_ratio": 0.79,
        "w_ratio": 0.1,
        "h_ratio": 0.18,
    },
    "window_title_keyword": "PAX1000",
    "server_host":          "0.0.0.0",
    "server_port":          10010,
    "auto_interval":        2.0,
    "output_dir":           "output",
    "output_prefix":        "pax1000",
    "remote_screenshot_dir": "remote_screenshots",
    "single_attempts":      3,
}


def load_config() -> dict:
    cfg = DEFAULT_CONFIG.copy()
    cfg["roi_config"] = DEFAULT_CONFIG["roi_config"].copy()
    if os.path.isfile(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            for k, v in loaded.items():
                if k == "roi_config" and isinstance(v, dict):
                    cfg["roi_config"].update(v)
                else:
                    cfg[k] = v
        except Exception as e:
            logging.warning("读取 config.json 失败: %s，使用默认配置", e)
    return cfg


def save_config(cfg: dict) -> None:
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ─── 主应用 ───────────────────────────────────────────────────────────────────

class PAX1000App:
    """PAX1000 数据采集系统主窗口"""

    def __init__(self) -> None:
        self.config = load_config()

        # 共享状态（多线程访问需持 _lock）
        self._lock                          = threading.Lock()
        self._capture_lock                  = threading.Lock()
        self.latest_reading: Optional[PAX1000Reading] = None
        self.auto_running                   = False
        self._stop_evt                      = threading.Event()

        # 输出文件句柄（自动模式使用）
        self._csv_file   = None
        self._csv_writer = None
        self._jsonl_file = None

        # PAX1000 读取器
        self.reader = self._make_reader()

        # 构建界面
        self.root = tk.Tk()
        self._build_ui()

        # 后台启动 TCP 服务器
        self._srv_sock: Optional[socket.socket] = None
        threading.Thread(target=self._server_loop, daemon=True,
                         name="TCPServer").start()

    # ── Reader 工厂 ──────────────────────────────────────────────────────────

    APP_TITLE = "PAX1000 数据采集系统"

    def _make_reader(self) -> PAX1000Reader:
        r = PAX1000Reader(
            roi_config=self.config.get("roi_config"),
            tesseract_cmd=self.config.get("tesseract_cmd"),
        )
        kw = self.config.get("window_title_keyword")
        if kw:
            r.WINDOW_TITLE_KEYWORD = kw
        return r

    # ── GUI 构建 ─────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.root.title(self.APP_TITLE)
        self.root.geometry("920x620")
        self.root.minsize(750, 520)

        try:
            ttk.Style().theme_use("vista")
        except Exception:
            pass

        self._build_toolbar()
        self._build_body()
        self._build_statusbar()
        self._refresh_mode_panel()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── 工具栏 ───────────────────────────────────────────────────────────────

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self.root, padding=(8, 5))
        bar.pack(fill=tk.X)

        ttk.Label(bar, text="模式：").pack(side=tk.LEFT)
        self._mode_var = tk.StringVar(value="single")
        ttk.Radiobutton(bar, text="单次采集", variable=self._mode_var,
                        value="single", command=self._refresh_mode_panel
                        ).pack(side=tk.LEFT, padx=2)
        ttk.Radiobutton(bar, text="自动循环", variable=self._mode_var,
                        value="auto", command=self._refresh_mode_panel
                        ).pack(side=tk.LEFT, padx=2)

        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=10)
        ttk.Button(bar, text="⚙ 配置",
                   command=self._open_config_dialog).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="🎯 ROI 校准",
                   command=self._run_calibrate).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="▶ 静默安装 Tesseract",
                   command=self._do_silent_install).pack(side=tk.LEFT, padx=4)

        self._srv_lbl = ttk.Label(bar, text="● 服务器启动中…", foreground="gray")
        self._srv_lbl.pack(side=tk.RIGHT, padx=10)

    # ── 主体布局 ─────────────────────────────────────────────────────────────

    def _build_body(self) -> None:
        body = ttk.Frame(self.root)
        body.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 4))

        # 左侧：控制面板（固定宽度）
        self._left = ttk.Frame(body, width=205)
        self._left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 5))
        self._left.pack_propagate(False)
        self._build_left_panel()

        # 右侧：数据展示
        right = ttk.Frame(body)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._build_right_panel(right)

    def _build_left_panel(self) -> None:
        lf = self._left

        # ── 自动循环控件 ──
        self._auto_frame = ttk.LabelFrame(lf, text="自动循环设置", padding=8)
        ttk.Label(self._auto_frame, text="采集间隔（秒）：").pack(anchor=tk.W)
        self._interval_var = tk.DoubleVar(value=self.config.get("auto_interval", 2.0))
        ttk.Spinbox(self._auto_frame, from_=0.5, to=600.0, increment=0.5,
                    textvariable=self._interval_var, width=10
                    ).pack(anchor=tk.W, pady=(2, 6))
        self._auto_btn = ttk.Button(self._auto_frame, text="▶ 开始采集",
                                    command=self._toggle_auto)
        self._auto_btn.pack(fill=tk.X)

        # ── 单次采集控件 ──
        self._single_frame = ttk.LabelFrame(lf, text="单次采集设置", padding=8)
        self._capture_btn = ttk.Button(self._single_frame, text="📷 采集一次",
                                       command=self._single_capture_threaded)
        self._capture_btn.pack(fill=tk.X)
        ttk.Label(self._single_frame, text="最少识别次数（取众数）：").pack(
            anchor=tk.W, pady=(8, 0))
        self._attempts_var = tk.IntVar(value=self.config.get("single_attempts", 3))
        ttk.Spinbox(self._single_frame, from_=3, to=9, increment=2,
                    textvariable=self._attempts_var, width=8).pack(anchor=tk.W)

        # ── 输出文件设置 ──
        out = ttk.LabelFrame(lf, text="输出文件", padding=8)
        out.pack(fill=tk.X, padx=4, pady=(8, 0))

        ttk.Label(out, text="保存目录：").pack(anchor=tk.W)
        row = ttk.Frame(out)
        row.pack(fill=tk.X)
        self._dir_var = tk.StringVar(value=self.config.get("output_dir", "output"))
        ttk.Entry(row, textvariable=self._dir_var).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(row, text="…", width=2,
                   command=self._browse_dir).pack(side=tk.RIGHT, padx=(2, 0))

        ttk.Label(out, text="文件名前缀：").pack(anchor=tk.W, pady=(6, 0))
        self._prefix_var = tk.StringVar(value=self.config.get("output_prefix", "pax1000"))
        ttk.Entry(out, textvariable=self._prefix_var).pack(fill=tk.X)

        ttk.Separator(lf).pack(fill=tk.X, padx=4, pady=10)
        ttk.Button(lf, text="🗑  清空历史列表",
                   command=self._clear_history).pack(fill=tk.X, padx=4)

    def _build_right_panel(self, parent: ttk.Frame) -> None:
        # ── 当前读数卡片 ──
        card = ttk.LabelFrame(parent, text="当前读数", padding=10)
        card.pack(fill=tk.X, pady=(0, 6))

        self._dv: dict[str, tk.StringVar] = {}
        fields = [
            ("time",        "采集时间",   4),
            ("orientation", "偏振态",     4),
            ("wavelength",  "波长",       4),
            ("power",       "功率",       4),
            ("dop",         "DOP",        4),
            ("s1",          "S1",         4),
            ("s2",          "S2",         4),
            ("s3",          "S3",         4),
        ]
        for idx, (key, label, _) in enumerate(fields):
            r, c = divmod(idx, 4)
            ttk.Label(card, text=f"{label}：", anchor=tk.E, width=7
                      ).grid(row=r, column=c * 2, sticky=tk.E,
                             padx=(8, 2), pady=3)
            v = tk.StringVar(value="--")
            ttk.Label(card, textvariable=v, anchor=tk.W, width=19,
                      font=("Consolas", 9, "bold")
                      ).grid(row=r, column=c * 2 + 1, sticky=tk.W,
                             padx=(0, 8))
            self._dv[key] = v

        # ── 历史记录表 ──
        hist = ttk.LabelFrame(parent, text="历史记录", padding=4)
        hist.pack(fill=tk.BOTH, expand=True)

        cols = ("采集时间", "偏振态", "波长(nm)", "功率(dBm)", "DOP(%)",
                "S1", "S2", "S3")
        widths = (145, 80, 80, 90, 75, 70, 70, 70)
        self._tree = ttk.Treeview(hist, columns=cols, show="headings")
        for col, w in zip(cols, widths):
            self._tree.heading(col, text=col)
            self._tree.column(col, width=w, anchor=tk.CENTER, minwidth=50)

        vsb = ttk.Scrollbar(hist, orient=tk.VERTICAL, command=self._tree.yview)
        hsb = ttk.Scrollbar(hist, orient=tk.HORIZONTAL, command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=0, column=0, sticky=tk.NSEW)
        vsb.grid(row=0, column=1, sticky=tk.NS)
        hsb.grid(row=1, column=0, sticky=tk.EW)
        hist.rowconfigure(0, weight=1)
        hist.columnconfigure(0, weight=1)

    def _build_statusbar(self) -> None:
        bar = ttk.Frame(self.root, relief=tk.SUNKEN, padding=(6, 2))
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        self._status_var = tk.StringVar(value="就绪")
        ttk.Label(bar, textvariable=self._status_var).pack(side=tk.LEFT)

    # ── 模式面板切换 ─────────────────────────────────────────────────────────

    def _refresh_mode_panel(self) -> None:
        mode = self._mode_var.get()
        if mode == "auto":
            self._single_frame.pack_forget()
            self._auto_frame.pack(fill=tk.X, padx=4, pady=(4, 0))
        else:
            self._auto_frame.pack_forget()
            self._single_frame.pack(fill=tk.X, padx=4, pady=(4, 0))
            if self.auto_running:
                self._stop_auto()

    # ── 数据显示辅助 ─────────────────────────────────────────────────────────

    @staticmethod
    def _fmt(v, digits: int = 4) -> str:
        return "--" if v is None else f"{v:.{digits}f}"

    def _show_reading(self, r: PAX1000Reading) -> None:
        """在主线程中刷新「当前读数」并向历史表头部插入一行。"""
        dt      = datetime.fromtimestamp(r.timestamp)
        ts_long = dt.strftime("%Y-%m-%d %H:%M:%S.%f")
        ts_hms  = dt.strftime("%H:%M:%S")

        self._dv["time"].set(ts_long)
        self._dv["orientation"].set(r.orientation or "--")
        self._dv["wavelength"].set(
            f"{self._fmt(r.wavelength)} {r.wavelength_unit}".strip())
        self._dv["power"].set(
            f"{self._fmt(r.power)} {r.power_unit}".strip())
        self._dv["dop"].set(self._fmt(r.dop))
        self._dv["s1"].set(self._fmt(r.s1))
        self._dv["s2"].set(self._fmt(r.s2))
        self._dv["s3"].set(self._fmt(r.s3))

        self._tree.insert("", 0, values=(
            ts_hms,
            r.orientation or "--",
            self._fmt(r.wavelength),
            self._fmt(r.power),
            self._fmt(r.dop),
            self._fmt(r.s1),
            self._fmt(r.s2),
            self._fmt(r.s3),
        ))
        children = self._tree.get_children()
        if len(children) > 2000:
            self._tree.delete(children[-1])

    def _clear_history(self) -> None:
        for item in self._tree.get_children():
            self._tree.delete(item)

    def _set_status(self, msg: str) -> None:
        self.root.after(0, lambda m=msg: self._status_var.set(m))

    # ── 自动循环采集 ─────────────────────────────────────────────────────────

    def _toggle_auto(self) -> None:
        if self.auto_running:
            self._stop_auto()
        else:
            self._start_auto()

    def _start_auto(self) -> None:
        out_dir  = self._dir_var.get() or "output"
        prefix   = self._prefix_var.get() or "pax1000"
        interval = max(0.5, float(self._interval_var.get()))

        os.makedirs(out_dir, exist_ok=True)
        stamp      = datetime.now().strftime("%Y%m%d_%H%M%S")
        csv_path   = os.path.join(out_dir, f"{prefix}_{stamp}.csv")
        jsonl_path = os.path.join(out_dir, f"{prefix}_{stamp}.jsonl")

        try:
            self._csv_file   = open(csv_path,   "w", newline="", encoding="utf-8")
            self._jsonl_file = open(jsonl_path, "a",             encoding="utf-8")
        except Exception as e:
            messagebox.showerror("无法创建输出文件", str(e))
            return

        fieldnames = ["datetime", "orientation", "wavelength", "wavelength_unit",
                      "power", "power_unit", "dop", "s1", "s2", "s3"]
        self._csv_writer = csv.DictWriter(self._csv_file, fieldnames=fieldnames)
        self._csv_writer.writeheader()
        self._csv_file.flush()

        self.auto_running = True
        self._stop_evt.clear()
        self._auto_btn.config(text="■ 停止采集")

        threading.Thread(target=self._auto_loop, args=(interval,),
                         daemon=True, name="AutoCapture").start()

        self._set_status(
            f"自动采集中  间隔={interval}s  "
            f"→ {os.path.basename(csv_path)}, {os.path.basename(jsonl_path)}")

    def _stop_auto(self) -> None:
        self._stop_evt.set()
        self.auto_running = False
        self._auto_btn.config(text="▶ 开始采集")
        for f in (self._csv_file, self._jsonl_file):
            try:
                if f:
                    f.close()
            except Exception:
                pass
        self._csv_file = self._jsonl_file = self._csv_writer = None
        self._set_status("自动采集已停止")

    def _auto_loop(self, interval: float) -> None:
        while not self._stop_evt.is_set():
            t0 = time.monotonic()
            try:
                r = self.reader.read_once()
                with self._lock:
                    self.latest_reading = r
                self._write_reading(r)
                self.root.after(0, lambda rr=r: self._show_reading(rr))
            except Exception as e:
                self._set_status(f"采集错误：{e}")
            elapsed = time.monotonic() - t0
            self._stop_evt.wait(max(0.0, interval - elapsed))

    def _write_reading(self, r: PAX1000Reading) -> None:
        """将读数追加写入 CSV 和 JSONL（长时间戳格式）。"""
        dt_str = datetime.fromtimestamp(r.timestamp).strftime("%Y-%m-%d %H:%M:%S.%f")

        if self._csv_writer and self._csv_file:
            self._csv_writer.writerow({
                "datetime":      dt_str,
                "orientation":   r.orientation,
                "wavelength":    r.wavelength,
                "wavelength_unit": r.wavelength_unit,
                "power":         r.power,
                "power_unit":    r.power_unit,
                "dop":           r.dop,
                "s1":            r.s1,
                "s2":            r.s2,
                "s3":            r.s3,
            })
            self._csv_file.flush()

        if self._jsonl_file:
            d = r.to_dict()
            d.pop("timestamp", None)
            d["datetime"] = dt_str
            self._jsonl_file.write(json.dumps(d, ensure_ascii=False) + "\n")
            self._jsonl_file.flush()

    # ── 单次采集 ─────────────────────────────────────────────────────────────

    def _single_capture_threaded(self) -> None:
        """直接截取整个桌面进行识别，无需最小化窗口。"""
        self._capture_btn.config(state=tk.DISABLED)
        self._set_status("正在截图识别…")
        threading.Thread(
            target=self._single_capture_worker,
            daemon=True, name="SingleCapture").start()

    def _single_capture_worker(self) -> None:
        n = int(self._attempts_var.get())
        r, err = self._multi_attempt(n)
        if r:
            with self._lock:
                self.latest_reading = r
            self.root.after(0, lambda rr=r: self._show_reading(rr))
            self._set_status("单次采集成功")
        else:
            self._set_status(f"识别失败：{err}")
            self.root.after(
                0,
                lambda: messagebox.showwarning(
                    "识别失败",
                    f"连续 {n} 次识别均未成功。\n\n错误信息：{err}",
                ),
            )
        self.root.after(0, lambda: self._capture_btn.config(state=tk.NORMAL))

    def _multi_attempt(self, n: int = 3, save_full_path: str = None) -> tuple:
        """
        对同一场景做 n 次独立识别，取众数结果。
        返回 (PAX1000Reading, None) 或 (None, 错误信息)。
        """
        results, last_err = [], None
        saved_full = False
        with self._capture_lock:
            for _ in range(n):
                try:
                    results.append(self.reader.read_once())
                    if save_full_path and not saved_full:
                        saved_full = self.reader.save_last_full_screenshot(save_full_path)
                except Exception as e:
                    last_err = str(e)

        if not results:
            return None, last_err or "全部识别失败"

        def _key(r: PAX1000Reading):
            return (
                r.orientation,
                round(r.wavelength, 1) if r.wavelength is not None else None,
                round(r.power,      1) if r.power      is not None else None,
            )

        best_key = Counter(_key(r) for r in results).most_common(1)[0][0]
        winner   = next(r for r in results if _key(r) == best_key)
        return winner, None

    # ── ROI 校准 ─────────────────────────────────────────────────────────────

    def _run_calibrate(self) -> None:
        """直接截取整个桌面进行校准，无需最小化窗口。"""
        out_dir   = self._dir_var.get() or "."
        os.makedirs(out_dir, exist_ok=True)
        save_path = os.path.join(out_dir, "pax1000_calibrate.png")

        def worker():
            self._set_status("正在截图校准，请确保 PAX1000 窗口已打开…")
            try:
                self.reader.calibrate(save_path=save_path)
                self._set_status(f"校准图已保存：{save_path}")
                self.root.after(0, lambda: self._calibrate_done(save_path))
            except Exception as e:
                msg = str(e)
                self._set_status(f"校准失败：{msg}")
                self.root.after(0, lambda m=msg: messagebox.showerror("校准失败", m))

        threading.Thread(target=worker, daemon=True, name="Calibrate").start()

    def _calibrate_done(self, save_path: str) -> None:
        """校准完成弹窗：居中显示，路径可选中复制，提供「打开图片」大按钮。"""
        dlg = tk.Toplevel(self.root)
        dlg.title("校准完成")
        dlg.resizable(False, False)
        dlg.transient(self.root)

        # ── 内容 ──
        pad = dict(padx=18, pady=6)

        ttk.Label(dlg, text="✅  校准图已保存至：",
                  font=("", 10, "bold")).pack(anchor=tk.W, padx=18, pady=(16, 2))

        path_var = tk.StringVar(value=save_path)
        entry = ttk.Entry(dlg, textvariable=path_var, state="readonly", width=58)
        entry.pack(fill=tk.X, padx=18, pady=(0, 4))

        ttk.Label(
            dlg,
            text="绿框 = 当前 ROI。\n"
                 "请打开图片确认绿框是否对准 PAX1000 左下角浮窗。\n"
                 "若有偏移，请在「⚙ 配置 → ROI 配置」中调整比例值后重新校准。",
            foreground="#555",
            justify=tk.LEFT,
        ).pack(anchor=tk.W, **pad)

        ttk.Separator(dlg).pack(fill=tk.X, padx=18, pady=6)

        btn_bar = ttk.Frame(dlg)
        btn_bar.pack(pady=(0, 16))

        ttk.Button(
            btn_bar,
            text="  🖼  打开校准图  ",
            command=lambda: os.startfile(save_path),
            width=18,
        ).pack(side=tk.LEFT, padx=10, ipady=6)

        ttk.Button(
            btn_bar,
            text="  关闭  ",
            command=dlg.destroy,
            width=10,
        ).pack(side=tk.LEFT, padx=10, ipady=6)

        # ── 居中到主窗口 ──
        dlg.update_idletasks()
        rw = dlg.winfo_reqwidth()
        rh = dlg.winfo_reqheight()
        rx = self.root.winfo_x() + (self.root.winfo_width()  - rw) // 2
        ry = self.root.winfo_y() + (self.root.winfo_height() - rh) // 2
        dlg.geometry(f"{rw}x{rh}+{rx}+{ry}")
        dlg.grab_set()

    # ── 静默安装 Tesseract ───────────────────────────────────────────────────

    def _do_silent_install(self) -> None:
        def worker():
            self._set_status("正在安装 Tesseract，请稍候…")
            try:
                ok = tess_install(force=False)
                if ok:
                    msg = "Tesseract 安装成功！\n请在「配置」中确认路径后重启。"
                    self._set_status("Tesseract 安装成功")
                    self.root.after(0, lambda: messagebox.showinfo("安装成功", msg))
                else:
                    msg = ("安装失败。\n请确认 installers/ 目录下存在 "
                           "tesseract-ocr-w64-setup*.exe 安装包。")
                    self._set_status("Tesseract 安装失败")
                    self.root.after(0, lambda: messagebox.showerror("安装失败", msg))
            except Exception as e:
                self._set_status(f"安装出错：{e}")
                self.root.after(0, lambda: messagebox.showerror("安装出错", str(e)))

        threading.Thread(target=worker, daemon=True, name="TessInstall").start()

    # ── 配置对话框 ───────────────────────────────────────────────────────────

    def _open_config_dialog(self) -> None:
        dlg = tk.Toplevel(self.root)
        dlg.title("系统配置")
        dlg.geometry("540x430")
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.resizable(False, False)

        nb = ttk.Notebook(dlg)
        nb.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # ── Tab 1：基本设置 ──
        t1 = ttk.Frame(nb, padding=14)
        nb.add(t1, text="基本设置")

        # Tesseract 路径
        ttk.Label(t1, text="Tesseract 路径：").grid(
            row=0, column=0, sticky=tk.W, pady=5)
        tess_var = tk.StringVar(value=self.config.get("tesseract_cmd", ""))
        r0 = ttk.Frame(t1)
        r0.grid(row=0, column=1, sticky=tk.EW)
        ttk.Entry(r0, textvariable=tess_var, width=36).pack(side=tk.LEFT)
        ttk.Button(r0, text="…", width=2,
                   command=lambda: self._pick_exe(tess_var, dlg)
                   ).pack(side=tk.LEFT, padx=2)

        # 服务端口
        ttk.Label(t1, text="TCP 监听端口：").grid(
            row=1, column=0, sticky=tk.W, pady=5)
        port_var = tk.IntVar(value=self.config.get("server_port", 5000))
        ttk.Spinbox(t1, from_=1024, to=65535,
                    textvariable=port_var, width=10
                    ).grid(row=1, column=1, sticky=tk.W)

        # 窗口标题关键字
        ttk.Label(t1, text="窗口标题关键字：").grid(
            row=2, column=0, sticky=tk.W, pady=5)
        kw_var = tk.StringVar(value=self.config.get("window_title_keyword", "PAX1000"))
        ttk.Entry(t1, textvariable=kw_var, width=22).grid(
            row=2, column=1, sticky=tk.W)

        # 单次识别次数
        ttk.Label(t1, text="单次最少识别次数：").grid(
            row=3, column=0, sticky=tk.W, pady=5)
        att_var = tk.IntVar(value=self.config.get("single_attempts", 3))
        ttk.Spinbox(t1, from_=3, to=9, increment=2,
                    textvariable=att_var, width=6
                    ).grid(row=3, column=1, sticky=tk.W)

        t1.columnconfigure(1, weight=1)

        # ── Tab 2：ROI 配置 ──
        t2 = ttk.Frame(nb, padding=14)
        nb.add(t2, text="ROI 配置")

        ttk.Label(t2, text="以下比例值相对于 PAX1000 窗口尺寸（范围 0.0 ~ 1.0）",
                  foreground="gray").pack(anchor=tk.W, pady=(0, 10))

        roi_cfg  = self.config.get("roi_config", DEFAULT_CONFIG["roi_config"])
        roi_vars: dict[str, tk.StringVar] = {}
        roi_defs = [
            ("x_ratio", "X 起点比例",  "浮窗左边缘 ÷ 窗口宽度"),
            ("y_ratio", "Y 起点比例",  "浮窗上边缘 ÷ 窗口高度"),
            ("w_ratio", "宽度比例",    "浮窗宽度 ÷ 窗口宽度"),
            ("h_ratio", "高度比例",    "浮窗高度 ÷ 窗口高度"),
        ]
        for key, label, hint in roi_defs:
            row = ttk.Frame(t2)
            row.pack(fill=tk.X, pady=5)
            ttk.Label(row, text=f"{label}：", width=14, anchor=tk.E).pack(side=tk.LEFT)
            v = tk.StringVar(value=str(roi_cfg.get(key, 0.0)))
            ttk.Entry(row, textvariable=v, width=10).pack(side=tk.LEFT, padx=6)
            ttk.Label(row, text=hint, foreground="gray").pack(side=tk.LEFT)
            roi_vars[key] = v

        # ── 底部按钮 ──
        btn_bar = ttk.Frame(dlg)
        btn_bar.pack(fill=tk.X, padx=8, pady=(0, 8))

        def do_save():
            try:
                self.config["tesseract_cmd"]        = tess_var.get().strip()
                self.config["server_port"]          = int(port_var.get())
                self.config["window_title_keyword"] = kw_var.get().strip()
                self.config["single_attempts"]      = int(att_var.get())
                self.config["roi_config"]           = {
                    k: float(v.get()) for k, v in roi_vars.items()
                }
                save_config(self.config)
                self.reader = self._make_reader()
                dlg.destroy()
                messagebox.showinfo("保存成功",
                                    "配置已写入 config.json。\n"
                                    "端口更改需重启软件生效。")
            except Exception as e:
                messagebox.showerror("保存失败", str(e))

        ttk.Button(btn_bar, text="保存", command=do_save).pack(side=tk.RIGHT, padx=4)
        ttk.Button(btn_bar, text="取消", command=dlg.destroy).pack(side=tk.RIGHT)

    def _pick_exe(self, var: tk.StringVar, parent: tk.Toplevel) -> None:
        init = os.path.dirname(var.get()) if var.get() else "C:\\"
        path = filedialog.askopenfilename(
            parent=parent,
            title="选择 tesseract.exe",
            filetypes=[("可执行文件", "*.exe"), ("所有文件", "*.*")],
            initialdir=init,
        )
        if path:
            var.set(path.replace("/", "\\"))

    def _browse_dir(self) -> None:
        d = filedialog.askdirectory(initialdir=self._dir_var.get() or ".")
        if d:
            self._dir_var.set(d)

    # ── TCP 服务器 ───────────────────────────────────────────────────────────

    def _server_loop(self) -> None:
        host = self.config.get("server_host", "0.0.0.0")
        port = int(self.config.get("server_port", 5000))
        try:
            self._srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._srv_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._srv_sock.bind((host, port))
            self._srv_sock.listen(10)
            self.root.after(
                0,
                lambda: self._srv_lbl.config(
                    text=f"● 服务器监听  {host}:{port}", foreground="green"),
            )
        except Exception as e:
            self.root.after(
                0,
                lambda: self._srv_lbl.config(
                    text=f"● 服务器启动失败：{e}", foreground="red"),
            )
            return

        while True:
            try:
                conn, _ = self._srv_sock.accept()
                threading.Thread(
                    target=self._handle_client, args=(conn,),
                    daemon=True, name="ClientHandler").start()
            except Exception:
                break

    def _handle_client(self, conn: socket.socket) -> None:
        """持久连接：循环接收多条请求，每条请求以换行符结尾，直到客户端断开。"""
        try:
            conn.settimeout(60.0)
            buf = b""
            while True:
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    # 超时不断开，继续等待
                    continue
                if not chunk:
                    # 客户端主动关闭
                    break
                buf += chunk
                # 按换行符切割，支持同一次 recv 携带多条消息
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        req = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
                        self._send(conn, {"IsSuccessful": False, "Value": "",
                                          "ErrorMessage": "请求不是合法的 JSON"})
                        continue
                    resp = self._dispatch(req)
                    self._send(conn, resp)

        except Exception:
            pass
        finally:
            try:
                conn.close()
            except Exception:
                pass

    @staticmethod
    def _send(conn: socket.socket, obj: dict) -> None:
        conn.sendall(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n")

    def _remote_screenshot_path(self, opcode: str) -> str:
        out_dir = self._dir_var.get() or self.config.get("output_dir", "output")
        screenshot_dir = self.config.get("remote_screenshot_dir", "remote_screenshots")
        if not os.path.isabs(screenshot_dir):
            screenshot_dir = os.path.join(out_dir, screenshot_dir)
        os.makedirs(screenshot_dir, exist_ok=True)

        prefix = self._prefix_var.get() or self.config.get("output_prefix", "pax1000")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return os.path.join(screenshot_dir, f"{prefix}_{opcode}_{stamp}.png")

    @staticmethod
    def _attach_screenshot_path(value: dict, screenshot_path: str) -> dict:
        if screenshot_path and os.path.isfile(screenshot_path):
            value["screenshot_path"] = screenshot_path
        return value

    def _dispatch(self, req: dict) -> dict:
        opcode = req.get("opcode", "")

        def ok(v=""):
            return {"IsSuccessful": True, "Value": v, "ErrorMessage": "Null"}

        def fail(msg):
            return {"IsSuccessful": False, "Value": "", "ErrorMessage": msg}

        # ── GetPAX1000Data ──
        if opcode == "GetPAX1000Data":
            if self._mode_var.get() == "auto":
                # 自动模式：返回最近缓存值
                with self._lock:
                    r = self.latest_reading
                if r is None:
                    return fail("自动采集尚未获得任何数据，请先点击「开始采集」")
                screenshot_path = self._remote_screenshot_path(opcode)
                self.reader.save_last_full_screenshot(screenshot_path)
                return ok(self._attach_screenshot_path(self._r2dict(r), screenshot_path))
            else:
                # 单次模式：触发新一次采集
                n = int(self.config.get("single_attempts", 3))
                screenshot_path = self._remote_screenshot_path(opcode)
                r, e = self._multi_attempt(n, save_full_path=screenshot_path)
                if r:
                    with self._lock:
                        self.latest_reading = r
                    self.root.after(0, lambda rr=r: self._show_reading(rr))
                    return ok(self._attach_screenshot_path(self._r2dict(r), screenshot_path))
                return fail(e or "识别失败")

        # ── CaptureOnce：强制一次单次采集 ──
        elif opcode == "CaptureOnce":
            n = int(self.config.get("single_attempts", 3))
            screenshot_path = self._remote_screenshot_path(opcode)
            r, e = self._multi_attempt(n, save_full_path=screenshot_path)
            if r:
                with self._lock:
                    self.latest_reading = r
                self.root.after(0, lambda rr=r: self._show_reading(rr))
                return ok(self._attach_screenshot_path(self._r2dict(r), screenshot_path))
            return fail(e or "识别失败")

        # ── GetStatus ──
        elif opcode == "GetStatus":
            with self._lock:
                r = self.latest_reading
            last_t = (datetime.fromtimestamp(r.timestamp)
                      .strftime("%Y-%m-%d %H:%M:%S.%f")) if r else None
            return ok({
                "mode":         self._mode_var.get(),
                "auto_running": self.auto_running,
                "last_time":    last_t,
            })

        # ── GetLastScreenshot：返回上次截图的图像数据流 ──
        elif opcode == "GetLastScreenshot":
            param = req.get("parameter") or {}
            specific_path = param.get("path", "")

            img_data: bytes = b""
            shot_path = ""

            if specific_path and os.path.isfile(specific_path):
                with open(specific_path, "rb") as f:
                    img_data = f.read()
                shot_path = specific_path
            elif self.reader.last_full_screenshot is not None:
                import cv2 as _cv2
                ok_enc, buf = _cv2.imencode(".png", self.reader.last_full_screenshot)
                if ok_enc:
                    img_data = bytes(buf)
                    shot_path = self._remote_screenshot_path(opcode)
                    with open(shot_path, "wb") as _f:
                        _f.write(img_data)
            else:
                out_dir = self._dir_var.get() or self.config.get("output_dir", "output")
                screenshot_dir = self.config.get("remote_screenshot_dir", "remote_screenshots")
                if not os.path.isabs(screenshot_dir):
                    screenshot_dir = os.path.join(out_dir, screenshot_dir)
                if os.path.isdir(screenshot_dir):
                    candidates = sorted(
                        [os.path.join(screenshot_dir, f)
                         for f in os.listdir(screenshot_dir)
                         if f.lower().endswith(".png")],
                        key=os.path.getmtime,
                    )
                    if candidates:
                        shot_path = candidates[-1]
                        with open(shot_path, "rb") as _f:
                            img_data = _f.read()

            if not img_data:
                return fail("尚无可用截图，请先触发一次采集操作")

            return ok({
                "image_format": "png",
                "image_size":   len(img_data),
                "image_base64": base64.b64encode(img_data).decode("ascii"),
                "screenshot_path": shot_path,
            })

        # ── check（心跳）──
        elif opcode == "check":
            return ok("PAX1000 采集系统 v1.0")

        else:
            return fail(f"未知 opcode: {opcode}")

    def _r2dict(self, r: PAX1000Reading) -> dict:
        """将读数转为可序列化字典，时间戳改为长格式字符串。"""
        d = r.to_dict()
        d.pop("timestamp", None)
        d["datetime"] = datetime.fromtimestamp(r.timestamp).strftime(
            "%Y-%m-%d %H:%M:%S.%f")
        return d

    # ── 关闭 ─────────────────────────────────────────────────────────────────

    def _on_close(self) -> None:
        if self.auto_running:
            self._stop_auto()
        if self._srv_sock:
            try:
                self._srv_sock.close()
            except Exception:
                pass
        self.root.destroy()

    # ── 启动 ─────────────────────────────────────────────────────────────────

    def run(self) -> None:
        self.root.mainloop()


# ─── 程序入口 ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="[%(levelname)s] %(name)s: %(message)s")
    app = PAX1000App()
    app.run()

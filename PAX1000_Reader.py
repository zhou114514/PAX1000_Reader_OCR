"""
PAX1000 界面数据读取器
截取浮窗 ROI -> 预处理 -> Tesseract OCR -> 正则解析

用法:
    python pax1000_reader.py                  # 单次读取
    python pax1000_reader.py calibrate        # 校准模式，截取窗口并标记 ROI
    python pax1000_reader.py loop 1.0         # 持续采集，间隔1秒
"""

import re
import sys
import time
import json
import cv2
import mss
import numpy as np
import win32gui
import pytesseract
from dataclasses import dataclass, asdict
from typing import Optional, Callable

# Windows DPI 感知：让 win32gui 返回物理像素坐标
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
except Exception:
    pass

from tesseract_setup import ensure_tesseract

# pytesseract.pytesseract.tesseract_cmd = ensure_tesseract()
tesseract_cmd = r"D:\TesseractOCR\tesseract.exe"
pytesseract.pytesseract.tesseract_cmd = tesseract_cmd


@dataclass
class PAX1000Reading:
    orientation: Optional[str] = None
    wavelength: Optional[float] = None
    wavelength_unit: str = "nm"
    power: Optional[float] = None
    power_unit: str = "dBm"
    dop: Optional[float] = None
    s1: Optional[float] = None
    s2: Optional[float] = None
    s3: Optional[float] = None
    raw_text: str = ""
    timestamp: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("raw_text")
        return d


class PAX1000Reader:
    """读取 PAX1000 软件左下角浮窗中的测量数据"""

    # 浮窗相对于窗口的默认位置（全部使用比例，自动适应任意分辨率）
    # 基准：1920×1080 最大化窗口下实测值反推
    #   x_offset=4px  → x_ratio = 4/1920 ≈ 0.0021
    #   width=175px   → w_ratio = 175/1920 ≈ 0.0911
    #   height=155px  → h_ratio = 155/1080 ≈ 0.1435
    # 首次使用请先运行 calibrate() 确认绿框是否对准浮窗
    DEFAULT_ROI = {
        "x_ratio": 0.0045,   # 浮窗左边缘 = 窗口宽度 × 此比例
        "y_ratio": 0.79,     # 浮窗顶部   = 窗口高度 × 此比例
        "w_ratio": 0.1,   # 浮窗宽度   = 窗口宽度 × 此比例
        "h_ratio": 0.18,   # 浮窗高度   = 窗口高度 × 此比例
    }

    WINDOW_TITLE_KEYWORD = "PAX1000"

    def __init__(self, roi_config: dict = None, tesseract_cmd: str = None):
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        self.roi = roi_config or self.DEFAULT_ROI.copy()

    # ──── 窗口定位 ────

    @staticmethod
    def get_monitor_info() -> list[dict]:
        """返回所有显示器的物理分辨率信息"""
        with mss.MSS() as sct:
            return [
                {
                    "index": i,
                    "left":   m["left"],
                    "top":    m["top"],
                    "width":  m["width"],
                    "height": m["height"],
                }
                for i, m in enumerate(sct.monitors[1:], start=1)  # monitors[0] 是所有屏幕合并区域
            ]

    def find_window(self) -> Optional[tuple]:
        """查找 PAX1000 窗口，返回 (left, top, right, bottom)"""
        result = []
        def _cb(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if self.WINDOW_TITLE_KEYWORD in title:
                    result.append(win32gui.GetWindowRect(hwnd))
        win32gui.EnumWindows(_cb, None)
        return result[0] if result else None

    def _roi_screen_rect(self, win_rect) -> dict:
        """根据比例 ROI 和窗口实际尺寸，计算屏幕绝对坐标"""
        wl, wt, wr, wb = win_rect
        win_w = wr - wl
        win_h = wb - wt
        return {
            "left":   wl + int(win_w * self.roi["x_ratio"]),
            "top":    wt + int(win_h * self.roi["y_ratio"]),
            "width":  int(win_w * self.roi["w_ratio"]),
            "height": int(win_h * self.roi["h_ratio"]),
        }

    # ──── 截图 & 预处理 ────

    def capture_roi(self, win_rect=None) -> np.ndarray:
        if win_rect is None:
            win_rect = self.find_window()
            if win_rect is None:
                raise RuntimeError("未找到 PAX1000 窗口，请确认软件已打开")
        rect = self._roi_screen_rect(win_rect)
        with mss.mss() as sct:
            shot = sct.grab(rect)
            return np.array(shot)[:, :, :3]

    @staticmethod
    def preprocess(img_bgr: np.ndarray) -> np.ndarray:
        """深色背景浅色文字 → 白底黑字 + 放大 + 去噪 + 锐化 + 二值化"""
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.bitwise_not(gray)
        # 放大 4× 更有利于细小字符识别
        gray = cv2.resize(gray, None, fx=4, fy=4,
                          interpolation=cv2.INTER_CUBIC)
        # CLAHE 自适应对比度增强
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)
        # 轻微去噪
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        # 锐化，提升笔画边缘清晰度
        sharpen_kernel = np.array([[-1, -1, -1],
                                   [-1,  9, -1],
                                   [-1, -1, -1]], dtype=np.float32)
        gray = cv2.filter2D(gray, -1, sharpen_kernel)
        gray = np.clip(gray, 0, 255).astype(np.uint8)
        _, bw = cv2.threshold(gray, 0, 255,
                              cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return bw

    # ──── OCR & 解析 ────

    # 允许出现的字符：数字、字母、常见标点及单位符号
    _OCR_WHITELIST = (
        "0123456789"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        ".:%-+() "
    )
    _OCR_CONFIG = (
        "--oem 1 --psm 6 "
        f"-c tessedit_char_whitelist={_OCR_WHITELIST}"
    )

    @staticmethod
    def ocr(img_bw: np.ndarray) -> str:
        return pytesseract.image_to_string(img_bw, config=PAX1000Reader._OCR_CONFIG)

    # ── 针对此界面字体的常见 OCR 错误纠正 ──
    _OCR_CORRECTIONS = [
        # s1 / s2 / s3 行首符号混淆
        (r'(?i)\bsl\s*:', 's1:'),          # sl: → s1:
        (r'(?i)\bs[lI|]\s*:', 's1:'),      # sI: / s|: → s1:
        (r'(?i)\(8([23])\s*:', r's\1:'),   # (82: → s2:   (83: → s3:
        (r'(?i)\b[cC]8([123])\s*:', r's\1:'),  # c81: 等 → s1:
        # 数字/字母混淆（仅在行首标签区域纠正，不影响数值）
        (r'(?m)^[Oo]rientation', 'Orientation'),
        (r'(?m)^[Ww]avelength', 'Wavelength'),
        (r'(?m)^[Pp]ower', 'Power'),
        (r'(?m)^DOP', 'DOP'),
    ]

    @classmethod
    def fix_ocr_text(cls, text: str) -> str:
        """对常见 OCR 识别错误做规则纠正"""
        import re as _re
        for pattern, repl in cls._OCR_CORRECTIONS:
            text = _re.sub(pattern, repl, text)
        return text

    @staticmethod
    def _parse_float(text: str, pattern: str) -> Optional[float]:
        m = re.search(pattern, text)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return None

    @classmethod
    def parse(cls, text: str) -> 'PAX1000Reading':
        # 先做文本纠错，再解析
        text_fixed = cls.fix_ocr_text(text)
        reading = PAX1000Reading(raw_text=text, timestamp=time.time())

        # Orientation
        m = re.search(r'[Oo]rientation[:\s]+(\w+)', text_fixed)
        if m:
            reading.orientation = m.group(1)

        # Wavelength
        m = re.search(r'[Ww]avelength[:\s]+([\d.]+)\s*(\w+)?', text_fixed)
        if m:
            reading.wavelength = cls._parse_float(text_fixed,
                r'[Ww]avelength[:\s]+([\d.]+)')
            if m.group(2):
                reading.wavelength_unit = m.group(2)

        # Power
        m = re.search(r'[Pp]ower[:\s]+([-\d.]+)\s*(\w+)?', text_fixed)
        if m:
            reading.power = cls._parse_float(text_fixed,
                r'[Pp]ower[:\s]+([-\d.]+)')
            if m.group(2):
                reading.power_unit = m.group(2)

        # DOP  （支持 "DOP: 24.11%" 或 "DOP: 24.11"）
        reading.dop = cls._parse_float(text_fixed,
            r'DOP[:\s]+([\d.]+)')

        # s1 / s2 / s3  — 容错：允许行首有 : 或空格噪声
        # 纠错后字段形如 "s1: 0.36"，但数值前可能混入 : 或空格
        reading.s1 = cls._parse_float(text_fixed,
            r's1\s*[:\s]\s*([-\d.]+)')
        reading.s2 = cls._parse_float(text_fixed,
            r's2\s*[:\s]\s*([-\d.]+)')
        reading.s3 = cls._parse_float(text_fixed,
            r's3\s*[:\s]\s*([-\d.]+)')

        return reading

    # ──── 对外接口 ────

    def read_once(self) -> PAX1000Reading:
        """单次读取，可直接集成到你的程序中"""
        img = self.capture_roi()
        bw  = self.preprocess(img)
        txt = self.ocr(bw)
        return self.parse(txt)

    def read_loop(self, interval: float = 1.0,
                  callback: Callable = None,
                  output_file: str = None):
        """
        持续循环采集

        Args:
            interval:    采样间隔（秒）
            callback:    可选回调 callback(PAX1000Reading)
            output_file: 可选，追加写入 JSONL 文件
        """
        print(f"持续采集中（间隔 {interval}s），Ctrl+C 停止 ...")
        fp = open(output_file, "a", encoding="utf-8") if output_file else None
        try:
            while True:
                try:
                    r = self.read_once()
                    line = json.dumps(r.to_dict(), ensure_ascii=False)
                    print(line)
                    if fp:
                        fp.write(line + "\n")
                        fp.flush()
                    if callback:
                        callback(r)
                except RuntimeError as e:
                    print(f"[!] {e}")
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n已停止。")
        finally:
            if fp:
                fp.close()

    # ──── 校准工具 ────

    def calibrate(self, save_path: str = "pax1000_calibrate.png"):
        """截取整个窗口，用绿框标记当前 ROI，保存图片供人工确认"""
        # 打印显示器信息
        monitors = self.get_monitor_info()
        print("=== 显示器信息 ===")
        for m in monitors:
            print(f"  显示器 {m['index']}: {m['width']}×{m['height']}  "
                  f"offset=({m['left']}, {m['top']})")

        win_rect = self.find_window()
        if win_rect is None:
            print("未找到 PAX1000 窗口！")
            return

        wl, wt, wr, wb = win_rect
        win_w, win_h = wr - wl, wb - wt
        print(f"\n=== PAX1000 窗口 ===")
        print(f"  位置: left={wl}  top={wt}  size={win_w}×{win_h}")

        with mss.MSS() as sct:
            full = sct.grab({"left": wl, "top": wt,
                             "width": win_w, "height": win_h})
            img = np.ascontiguousarray(np.array(full)[:, :, :3])

        roi = self._roi_screen_rect(win_rect)
        rx, ry = roi["left"] - wl, roi["top"] - wt
        cv2.rectangle(img, (rx, ry),
                      (rx + roi["width"], ry + roi["height"]),
                      (0, 255, 0), 2)
        cv2.imwrite(save_path, img)

        print(f"\n=== ROI（自动按窗口尺寸换算）===")
        print(f"  比例配置 : {json.dumps(self.roi)}")
        print(f"  实际像素 : x={rx}  y={ry}  "
              f"w={roi['width']}  h={roi['height']}")
        print(f"\n已保存校准图 → {save_path}")
        print("绿框 = 当前 ROI，请打开图片确认是否对准浮窗。")
        print("如有偏移，调整 roi_config 中的比例值后重新运行 calibrate。")


# ─── CLI 入口 ───

if __name__ == "__main__":
    reader = PAX1000Reader()

    if len(sys.argv) > 1 and sys.argv[1] == "calibrate":
        reader.calibrate()

    elif len(sys.argv) > 1 and sys.argv[1] == "loop":
        sec = float(sys.argv[2]) if len(sys.argv) > 2 else 1.0
        reader.read_loop(interval=sec, output_file="pax1000_data.jsonl")

    else:
        r = reader.read_once()
        print(f"Orientation : {r.orientation}")
        print(f"Wavelength  : {r.wavelength} {r.wavelength_unit}")
        print(f"Power       : {r.power} {r.power_unit}")
        print(f"DOP         : {r.dop} %")
        print(f"s1          : {r.s1}")
        print(f"s2          : {r.s2}")
        print(f"s3          : {r.s3}")
        print(f"--- 纠错后 OCR ---\n{PAX1000Reader.fix_ocr_text(r.raw_text)}")
        print(f"--- 原始 OCR ---\n{r.raw_text}")
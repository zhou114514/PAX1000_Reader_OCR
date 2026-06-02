"""
Tesseract OCR 自动安装模块

将 Tesseract 安装包（exe）放在本脚本同目录的 installers/ 文件夹中，
首次使用时自动静默安装。

用法:
    # 作为模块导入 —— 在业务代码启动时调用
    from tesseract_setup import ensure_tesseract
    ensure_tesseract()          # 未安装则自动静默安装

    # 直接运行 —— 手动触发安装/检查
    python tesseract_setup.py           # 检查并按需安装
    python tesseract_setup.py --force   # 强制重新安装
"""

import os
import sys
import glob
import subprocess
import logging

log = logging.getLogger(__name__)

# ─── 配置 ───────────────────────────────────────────────
INSTALL_DIR = r"C:\Program Files\Tesseract-OCR"
TESSERACT_EXE = os.path.join(INSTALL_DIR, "tesseract.exe")
INSTALLER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "installers")
INSTALLER_PATTERN = "tesseract-ocr-w64-setup*.exe"
# ────────────────────────────────────────────────────────


def is_installed() -> bool:
    """检查 Tesseract 是否已安装且可执行"""
    if not os.path.isfile(TESSERACT_EXE):
        return False
    try:
        result = subprocess.run(
            [TESSERACT_EXE, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def get_version() -> str:
    """获取已安装的 Tesseract 版本号"""
    try:
        result = subprocess.run(
            [TESSERACT_EXE, "--version"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip().splitlines()[0]
    except (OSError, subprocess.TimeoutExpired):
        pass
    return ""


def _find_installer() -> str:
    """在 installers/ 目录中查找安装包，返回路径"""
    if not os.path.isdir(INSTALLER_DIR):
        raise FileNotFoundError(
            f"安装包目录不存在: {INSTALLER_DIR}\n"
            f"请创建该目录并放入 Tesseract 安装包 ({INSTALLER_PATTERN})"
        )

    candidates = sorted(
        glob.glob(os.path.join(INSTALLER_DIR, INSTALLER_PATTERN)),
        key=os.path.getmtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(
            f"在 {INSTALLER_DIR} 中未找到匹配 {INSTALLER_PATTERN} 的安装包\n"
            f"请从 https://github.com/UB-Mannheim/tesseract/wiki 下载后放入该目录"
        )

    return candidates[0]


def install(force: bool = False) -> bool:
    """
    执行 Tesseract 静默安装。

    Args:
        force: 为 True 时即使已安装也重新安装

    Returns:
        True 表示安装成功
    """
    if not force and is_installed():
        ver = get_version()
        log.info("Tesseract 已安装: %s", ver)
        return True

    installer = _find_installer()
    log.info("正在静默安装 Tesseract: %s", os.path.basename(installer))

    cmd = [installer, "/S", "/ADDENVPATH"]

    try:
        proc = subprocess.run(cmd, timeout=120)
    except subprocess.TimeoutExpired:
        log.error("安装超时（120s），请手动运行安装包")
        return False

    if proc.returncode != 0:
        log.error("安装程序返回错误码 %d", proc.returncode)
        return False

    if not os.path.isfile(TESSERACT_EXE):
        log.error("安装完成但未找到 %s，请检查安装路径", TESSERACT_EXE)
        return False

    ver = get_version()
    log.info("Tesseract 安装成功: %s", ver)
    return True


def ensure_tesseract() -> str:
    """
    确保 Tesseract 可用，返回 tesseract.exe 的完整路径。

    - 已安装 → 直接返回路径
    - 未安装 → 自动静默安装后返回路径
    - 安装失败 → 抛出 RuntimeError
    """
    if is_installed():
        return TESSERACT_EXE

    log.warning("未检测到 Tesseract，正在自动安装...")
    if not install():
        raise RuntimeError(
            "Tesseract 自动安装失败，请手动安装:\n"
            "  1. 运行 installers/ 目录中的安装包\n"
            "  2. 或从 https://github.com/UB-Mannheim/tesseract/wiki 下载安装"
        )
    return TESSERACT_EXE


# ─── CLI 入口 ───────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(message)s",
    )

    force = "--force" in sys.argv

    if is_installed() and not force:
        print(f"[OK] Tesseract 已安装: {get_version()}")
        print(f"     路径: {TESSERACT_EXE}")
    else:
        action = "强制重新安装" if force else "开始安装"
        print(f"[...] {action} Tesseract ...")
        if install(force=force):
            print(f"[OK] 安装成功: {get_version()}")
        else:
            print("[FAIL] 安装失败，请查看上方错误信息")
            sys.exit(1)

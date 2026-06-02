# PAX1000 OCR 数据采集系统

本项目用于从 Thorlabs PAX1000 软件窗口中截取测量数据区域，通过 Tesseract OCR 识别偏振/光功率相关读数，并提供本地 GUI、自动循环采集、CSV/JSONL 落盘和 TCP JSON 远程调用接口。

## 功能概览

- 自动定位标题或窗口类名包含 `PAX1000` / `ThorlabsPAX1000` 的软件窗口。
- 按配置的 ROI 比例截取 PAX1000 左下角数据浮窗。
- 使用 OpenCV 对截图进行灰度、反色、放大、增强、锐化和二值化预处理。
- 使用 Tesseract OCR 识别 `Orientation`、`Wavelength`、`Power`、`DOP`、`s1`、`s2`、`s3`。
- GUI 支持单次采集、自动循环采集、ROI 校准、Tesseract 静默安装和系统配置。
- 自动循环模式会实时刷新界面，并写入 CSV 与 JSONL。
- 内置 TCP JSON 服务，供上位机、自动化脚本或其他系统远程读取当前数据。

## 运行环境

建议环境：

- Windows 10/11
- Python 3.9 或更高版本
- 已安装并可打开 Thorlabs PAX1000 官方软件
- 已安装 Tesseract OCR，或准备好 UB Mannheim 版本的 Windows 安装包

Python 依赖：

```powershell
python -m pip install opencv-python mss numpy pywin32 pytesseract
```

Tkinter 通常随 Windows 版 Python 一起安装。如果运行 GUI 时报 Tkinter 相关错误，请确认安装的是完整 Python 发行版。

## Tesseract OCR 准备

项目默认从 `config.json` 中读取 `tesseract_cmd`：

```json
{
  "tesseract_cmd": "D:\\TesseractOCR\\tesseract.exe"
}
```

可选安装方式：

- 手动安装 Tesseract，并在 GUI 的「配置」中填写 `tesseract.exe` 路径。
- 将 `tesseract-ocr-w64-setup*.exe` 放入项目同目录下的 `installers/` 文件夹，然后在 GUI 中点击「静默安装 Tesseract」。
- 直接运行安装检查脚本：

```powershell
python tesseract_setup.py
python tesseract_setup.py --force
```

## 快速开始

1. 打开 Thorlabs PAX1000 官方软件，确保主窗口未被完全遮挡。
2. 确认 `config.json` 中的 `tesseract_cmd` 指向有效的 `tesseract.exe`。
3. 启动采集系统：

```powershell
python PAX1000_App.py
```

4. 首次使用建议点击「ROI 校准」，检查生成的 `pax1000_calibrate.png`：
   - 蓝框表示 PAX1000 软件窗口。
   - 绿框表示当前 OCR 截取区域。
   - 如果绿框没有覆盖左下角数据浮窗，请在「配置」中调整 ROI 比例后重新校准。
5. 选择「单次采集」或「自动循环」开始读取数据。

## GUI 使用说明

### 单次采集

单次采集会对同一场景进行多次 OCR 识别，并按 `orientation`、`wavelength`、`power` 的组合取众数，降低偶发识别误差。识别次数由 `single_attempts` 控制，默认值为 `3`。

### 自动循环

自动循环会按设定间隔持续采集数据：

- 每次成功识别后刷新当前读数和历史记录。
- 数据写入 `output_dir` 下的 CSV 和 JSONL 文件。
- TCP 远程接口的 `GetPAX1000Data` 在自动模式下返回最近一次缓存值。

采集间隔由 `auto_interval` 控制，GUI 中可临时调整。

### ROI 校准

ROI 使用相对于 PAX1000 窗口尺寸的比例配置，而不是固定像素坐标，因此可以适配不同分辨率和窗口尺寸。

`roi_config` 中字段含义：

- `x_ratio`：ROI 左边缘相对窗口宽度的比例。
- `y_ratio`：ROI 顶部相对窗口高度的比例。
- `w_ratio`：ROI 宽度相对窗口宽度的比例。
- `h_ratio`：ROI 高度相对窗口高度的比例。

## 配置文件

配置文件为项目根目录下的 `config.json`。当前支持字段如下：

```json
{
  "tesseract_cmd": "D:\\TesseractOCR\\tesseract.exe",
  "roi_config": {
    "x_ratio": 0.0045,
    "y_ratio": 0.79,
    "w_ratio": 0.1,
    "h_ratio": 0.18
  },
  "window_title_keyword": "PAX1000",
  "server_host": "0.0.0.0",
  "server_port": 10010,
  "auto_interval": 2.0,
  "output_dir": "output",
  "output_prefix": "pax1000",
  "remote_screenshot_dir": "remote_screenshots",
  "single_attempts": 3
}
```

字段说明：

- `tesseract_cmd`：Tesseract 可执行文件路径。
- `roi_config`：OCR 截图区域比例配置。
- `window_title_keyword`：用于匹配 PAX1000 窗口标题的关键字。
- `server_host`：TCP 服务监听地址，默认 `0.0.0.0`。
- `server_port`：TCP 服务监听端口，默认 `10010`。
- `auto_interval`：自动循环采集间隔，单位秒。
- `output_dir`：自动采集输出目录。
- `output_prefix`：输出文件名前缀。
- `remote_screenshot_dir`：远程调用时保存截图的目录。相对路径会放在 `output_dir` 下。
- `single_attempts`：单次采集 OCR 尝试次数，建议使用奇数。

端口修改后需要重启程序才会生效。

## 输出文件

自动循环模式启动后，会在 `output_dir` 下创建带时间戳的文件：

```text
output/
  pax1000_YYYYMMDD_HHMMSS.csv
  pax1000_YYYYMMDD_HHMMSS.jsonl
```

CSV 字段：

- `datetime`
- `orientation`
- `wavelength`
- `wavelength_unit`
- `power`
- `power_unit`
- `dop`
- `s1`
- `s2`
- `s3`

JSONL 每行是一条 JSON 记录，字段与 CSV 基本一致。

远程调用 `GetPAX1000Data` 或 `CaptureOnce` 时，如果成功保存截图，返回值中会附带 `screenshot_path`。截图默认保存到：

```text
output/remote_screenshots/
```

## 远程接口

程序启动 GUI 后会自动启动 TCP JSON 服务。默认监听：

```text
0.0.0.0:10010
```

请求为单个 UTF-8 JSON 对象，响应为单行 UTF-8 JSON。详细协议见 `REMOTE_PROTOCOL.md`。

最小请求示例：

```json
{"opcode":"check","parameter":{}}
```

最小响应示例：

```json
{"IsSuccessful":true,"Value":"PAX1000 采集系统 v1.0","ErrorMessage":"Null"}
```

## 命令行读取器

除 GUI 外，也可以直接使用 `PAX1000_Reader.py`：

```powershell
python PAX1000_Reader.py
python PAX1000_Reader.py calibrate
python PAX1000_Reader.py loop 1.0
```

命令说明：

- 无参数：执行一次 OCR 并打印解析结果。
- `calibrate`：保存校准图 `pax1000_calibrate.png`。
- `loop 1.0`：按 1 秒间隔循环采集，并追加写入 `pax1000_data.jsonl`。

## 常见问题

### 未找到 PAX1000 窗口

请确认：

- PAX1000 官方软件已打开。
- 窗口标题中包含 `PAX1000`。
- 如实际标题不同，请修改 `config.json` 中的 `window_title_keyword`。

### OCR 结果为空或字段缺失

建议按顺序检查：

- `tesseract_cmd` 是否指向正确的 `tesseract.exe`。
- ROI 校准图中的绿框是否覆盖数据浮窗。
- PAX1000 窗口是否被其他窗口遮挡。
- 显示缩放或窗口布局变化后是否需要重新校准 ROI。

### 远程连接失败

请确认：

- GUI 程序正在运行。
- 界面右上角显示 TCP 服务已监听。
- `server_port` 与客户端连接端口一致。
- Windows 防火墙允许该端口入站连接。
- 如果只允许本机访问，可将 `server_host` 改为 `127.0.0.1`。

### 自动模式远程读取失败

`GetPAX1000Data` 在自动模式下只返回最近一次缓存值。如果还没有点击「开始采集」，或自动采集尚未成功得到第一条数据，会返回失败信息。

## 代码结构

- `PAX1000_App.py`：GUI 主程序、自动/单次采集、文件输出、TCP 服务。
- `PAX1000_Reader.py`：窗口定位、截图、OCR 预处理、文本解析、命令行工具。
- `tesseract_setup.py`：Tesseract 静默安装和检查工具。
- `config.json`：运行配置。
- `output/`：采集输出、校准图和远程调用截图目录。
- `LICENSE`：项目许可证，当前为 MPL 2.0。

## 注意事项

- 截图识别依赖屏幕可见内容，PAX1000 数据浮窗不能被完全遮挡。
- TCP 服务当前不包含认证、加密或访问控制，建议仅在受信任局域网或本机环境使用。
- OCR 识别受字体、缩放、窗口布局和屏幕渲染影响，部署到新电脑后应先执行 ROI 校准。
- 长时间自动采集会持续写入输出文件，请定期清理不再需要的 `output/` 数据。


# PAX1000 远程协议记录

协议版本：`1.2`

适用程序：`PAX1000_App.py`

记录日期：`2026-06-02`

## 1. 协议概述

PAX1000 OCR 数据采集系统启动后，会在后台开启一个 TCP 服务。客户端通过发送 UTF-8 编码的 JSON 请求获取采集数据、触发单次采集或查询运行状态。

当前协议为持久连接请求/响应模型：

- 传输层：TCP
- 数据编码：UTF-8
- 数据格式：JSON
- 默认监听地址：`0.0.0.0`
- 默认监听端口：`10010`
- 连接方式：**持久连接**，建立一次 TCP 连接后可连续发送多条请求，服务端保持连接直到客户端主动断开
- 空闲超时：60 秒内无数据到达时，服务端继续等待（不主动断开）

服务端每次响应一个 JSON 对象，并在末尾追加换行符 `\n`。

## 2. 连接参数

默认配置来自 `config.json`：

```json
{
  "server_host": "0.0.0.0",
  "server_port": 10010
}
```

说明：

- `server_host` 为 `0.0.0.0` 时，表示监听本机所有网卡。
- 如果只允许本机客户端访问，可改为 `127.0.0.1`。
- 修改 `server_port` 后需要重启 GUI 程序。
- 如果跨主机访问，需要确保 Windows 防火墙放行对应端口。

## 3. 报文边界

客户端发送单个 JSON 对象。服务端读取数据直到收到的内容去除尾部空白后以 `}` 结尾，或连接关闭。

推荐客户端在 JSON 后追加换行符，并在发送后等待服务端响应：

```json
{"opcode":"check","parameter":{}}
```

注意：

- 当前协议没有长度头。
- 当前协议不支持一个 TCP 连接内连续发送多个请求。
- 请求体必须是合法 JSON。
- JSON 字段名大小写敏感。

## 4. 请求格式

标准请求格式：

```json
{
  "opcode": "GetPAX1000Data",
  "parameter": {}
}
```

字段说明：

- `opcode`：必填，字符串，表示操作类型。
- `parameter`：可选，对象，预留扩展参数。当前实现未读取该字段。

当前支持的 `opcode`：

- `check`
- `GetStatus`
- `GetPAX1000Data`
- `CaptureOnce`
- `GetLastScreenshot`

## 5. 响应格式

所有响应均使用统一外层结构：

```json
{
  "IsSuccessful": true,
  "Value": {},
  "ErrorMessage": "Null"
}
```

字段说明：

- `IsSuccessful`：布尔值，表示请求是否成功。
- `Value`：成功时返回数据；失败时通常为空字符串 `""`。
- `ErrorMessage`：成功时为字符串 `"Null"`；失败时为错误说明。

失败响应示例：

```json
{
  "IsSuccessful": false,
  "Value": "",
  "ErrorMessage": "未知 opcode: UnknownCommand"
}
```

非法 JSON 响应示例：

```json
{
  "IsSuccessful": false,
  "Value": "",
  "ErrorMessage": "请求不是合法的 JSON"
}
```

## 6. 数据字段定义

`GetPAX1000Data` 和 `CaptureOnce` 成功时，`Value` 通常为读数对象：

```json
{
  "orientation": "Linear",
  "wavelength": 1550.0,
  "wavelength_unit": "nm",
  "power": -10.25,
  "power_unit": "dBm",
  "dop": 99.8,
  "s1": 0.1234,
  "s2": -0.5678,
  "s3": 0.9012,
  "datetime": "2026-06-02 11:31:41.123456",
  "screenshot_path": "output\\remote_screenshots\\pax1000_GetPAX1000Data_20260602_113141_123456.png"
}
```

字段说明：

- `orientation`：偏振方向或状态文本，来自 OCR 解析。
- `wavelength`：波长数值。
- `wavelength_unit`：波长单位，默认 `nm`。
- `power`：功率数值。
- `power_unit`：功率单位，默认 `dBm`。
- `dop`：偏振度数值。
- `s1`：Stokes 参数 `s1`。
- `s2`：Stokes 参数 `s2`。
- `s3`：Stokes 参数 `s3`。
- `datetime`：采集时间，格式为 `YYYY-MM-DD HH:MM:SS.ffffff`。
- `screenshot_path`：可选字段。远程调用时如果成功保存用于 OCR 的全屏截图，则返回该路径。

识别不到的字段可能为 `null`。

## 7. opcode 说明

### 7.1 check

用途：心跳检测，确认 TCP 服务可访问。

请求：

```json
{
  "opcode": "check",
  "parameter": {}
}
```

成功响应：

```json
{
  "IsSuccessful": true,
  "Value": "PAX1000 采集系统 v1.0",
  "ErrorMessage": "Null"
}
```

### 7.2 GetStatus

用途：查询 GUI 当前运行状态。

请求：

```json
{
  "opcode": "GetStatus",
  "parameter": {}
}
```

成功响应示例：

```json
{
  "IsSuccessful": true,
  "Value": {
    "mode": "auto",
    "auto_running": true,
    "last_time": "2026-06-02 11:31:41.123456"
  },
  "ErrorMessage": "Null"
}
```

`Value` 字段说明：

- `mode`：GUI 当前模式，可能为 `single` 或 `auto`。
- `auto_running`：自动循环采集是否正在运行。
- `last_time`：最近一次成功采集的时间。尚无数据时为 `null`。

### 7.3 GetPAX1000Data

用途：获取 PAX1000 读数。

请求：

```json
{
  "opcode": "GetPAX1000Data",
  "parameter": {}
}
```

行为说明：

- GUI 处于自动循环模式时，返回最近一次自动采集缓存值。
- GUI 处于单次采集模式时，立即触发一次多次 OCR 识别，并返回众数结果。
- 自动模式下如果尚未获得任何数据，会返回失败。
- 成功时会尝试保存最近一次全屏截图，并在 `Value.screenshot_path` 中返回路径。

自动模式尚无缓存时的失败响应：

```json
{
  "IsSuccessful": false,
  "Value": "",
  "ErrorMessage": "自动采集尚未获得任何数据，请先点击「开始采集」"
}
```

成功响应示例：

```json
{
  "IsSuccessful": true,
  "Value": {
    "orientation": "Linear",
    "wavelength": 1550.0,
    "wavelength_unit": "nm",
    "power": -10.25,
    "power_unit": "dBm",
    "dop": 99.8,
    "s1": 0.1234,
    "s2": -0.5678,
    "s3": 0.9012,
    "datetime": "2026-06-02 11:31:41.123456",
    "screenshot_path": "output\\remote_screenshots\\pax1000_GetPAX1000Data_20260602_113141_123456.png"
  },
  "ErrorMessage": "Null"
}
```

### 7.4 CaptureOnce

用途：无论 GUI 当前处于单次模式还是自动模式，都强制触发一次新的 OCR 采集。

请求：

```json
{
  "opcode": "CaptureOnce",
  "parameter": {}
}
```

行为说明：

- 会使用 `single_attempts` 配置执行多次识别。
- 成功后更新 GUI 中的最近读数。
- 成功时会尝试保存本次截图，并在 `Value.screenshot_path` 中返回路径。
- 如果 PAX1000 窗口未找到、ROI 越界或 OCR 全部失败，会返回失败响应。

失败响应示例：

```json
{
  "IsSuccessful": false,
  "Value": "",
  "ErrorMessage": "未找到 PAX1000 窗口，请确认软件已打开"
}
```

### 7.5 GetLastScreenshot

用途：获取上次保存的全屏截图，图像数据以 Base64 编码内嵌于响应包内。

请求：

```json
{
  "opcode": "GetLastScreenshot",
  "parameter": {}
}
```

也可通过 `parameter.path` 指定想要获取的截图文件路径（必须是服务端本地可访问的绝对或相对路径）：

```json
{
  "opcode": "GetLastScreenshot",
  "parameter": {
    "path": "output\\remote_screenshots\\pax1000_GetPAX1000Data_20260602_113141_123456.png"
  }
}
```

行为说明：

- 优先使用 `parameter.path` 指定的文件（若存在）。
- 若未指定 `path`，则使用最近一次截图操作留存在内存中的全屏图像（`reader.last_full_screenshot`），并将其保存到 `remote_screenshots` 目录后返回。
- 若内存中无截图，则自动在 `remote_screenshots` 目录中按修改时间选取最新的 `.png` 文件。
- 若以上三种来源均无可用截图，返回失败响应。
- 图像以 PNG 格式编码，并以 Base64 字符串内嵌于 `Value.image_base64`。
- 注意：全屏截图体积通常在 1 MB～10 MB 之间，Base64 后约增大 33%，客户端接收时需适当调大 recv 缓冲区或循环读取直至收到换行符。

成功响应示例：

```json
{
  "IsSuccessful": true,
  "Value": {
    "image_format": "png",
    "image_size": 1572864,
    "image_base64": "iVBORw0KGgoAAAANSUhEUgAA...(省略)...",
    "screenshot_path": "output\\remote_screenshots\\pax1000_GetLastScreenshot_20260602_113141_123456.png"
  },
  "ErrorMessage": "Null"
}
```

`Value` 字段说明：

| 字段 | 类型 | 说明 |
|------|------|------|
| `image_format` | string | 固定为 `"png"` |
| `image_size` | integer | PNG 文件字节数（Base64 解码后的原始大小） |
| `image_base64` | string | Base64 编码的 PNG 图像数据 |
| `screenshot_path` | string | 截图在服务端保存的路径；若使用指定 path 则与请求一致 |

无可用截图的失败响应：

```json
{
  "IsSuccessful": false,
  "Value": "",
  "ErrorMessage": "尚无可用截图，请先触发一次采集操作"
}
```

## 8. 客户端示例

### 8.1 Python 示例

```python
import json
import socket


def call_pax1000(host="127.0.0.1", port=10010, opcode="GetPAX1000Data"):
    req = {
        "opcode": opcode,
        "parameter": {},
    }

    with socket.create_connection((host, port), timeout=10) as sock:
        sock.sendall(json.dumps(req, ensure_ascii=False).encode("utf-8") + b"\n")

        chunks = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break

    raw = b"".join(chunks).decode("utf-8").strip()
    return json.loads(raw)


if __name__ == "__main__":
    print(call_pax1000(opcode="check"))
    print(call_pax1000(opcode="GetStatus"))
    print(call_pax1000(opcode="GetPAX1000Data"))
```

### 8.2 Python 示例：接收并保存截图

```python
import json
import socket
import base64


def get_last_screenshot(host="127.0.0.1", port=10010,
                        save_path="last_screenshot.png",
                        specific_path=""):
    """获取服务端最近一次截图并保存到本地。"""
    param = {}
    if specific_path:
        param["path"] = specific_path

    req = {"opcode": "GetLastScreenshot", "parameter": param}

    with socket.create_connection((host, port), timeout=30) as sock:
        sock.sendall(json.dumps(req, ensure_ascii=False).encode("utf-8") + b"\n")

        # 截图响应体积较大，需循环接收直到换行符
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
            if b"\n" in chunk:
                break

    raw = b"".join(chunks).decode("utf-8").strip()
    resp = json.loads(raw)

    if not resp["IsSuccessful"]:
        raise RuntimeError(resp["ErrorMessage"])

    img_bytes = base64.b64decode(resp["Value"]["image_base64"])
    with open(save_path, "wb") as f:
        f.write(img_bytes)

    print(f"截图已保存：{save_path}（{len(img_bytes)} 字节）")
    print(f"服务端路径：{resp['Value']['screenshot_path']}")
    return save_path


if __name__ == "__main__":
    get_last_screenshot()
```

### 8.3 PowerShell 示例

```powershell
$client = [System.Net.Sockets.TcpClient]::new("127.0.0.1", 10010)
$stream = $client.GetStream()
$request = '{"opcode":"check","parameter":{}}' + "`n"
$bytes = [System.Text.Encoding]::UTF8.GetBytes($request)
$stream.Write($bytes, 0, $bytes.Length)

$buffer = New-Object byte[] 4096
$count = $stream.Read($buffer, 0, $buffer.Length)
$response = [System.Text.Encoding]::UTF8.GetString($buffer, 0, $count)
$response

$stream.Close()
$client.Close()
```

## 9. 错误与异常约定

常见失败原因：

- 请求不是合法 JSON。
- `opcode` 未知。
- PAX1000 官方软件未打开。
- PAX1000 窗口被最小化后无法正常恢复，或被其他窗口遮挡。
- ROI 配置不正确，导致截图区域无效或超出屏幕。
- Tesseract 路径配置错误。
- OCR 多次尝试均未成功解析出有效结果。
- 自动模式下尚未采集到第一条缓存数据。

客户端处理建议：

- 先调用 `check` 确认服务在线。
- 再调用 `GetStatus` 判断 GUI 当前模式和最近采集时间。
- 自动模式下优先使用 `GetPAX1000Data` 获取缓存，避免频繁触发 OCR。
- 需要强制刷新数据时使用 `CaptureOnce`。
- 当 `IsSuccessful` 为 `false` 时，不要解析 `Value` 为读数对象，应直接展示或记录 `ErrorMessage`。

## 10. 截图路径约定

远程调用保存截图的目录由 `remote_screenshot_dir` 控制：

```json
{
  "output_dir": "output",
  "output_prefix": "pax1000",
  "remote_screenshot_dir": "remote_screenshots"
}
```

当 `remote_screenshot_dir` 是相对路径时，会拼接到 `output_dir` 下：

```text
output/remote_screenshots/
```

文件名格式：

```text
{output_prefix}_{opcode}_{YYYYMMDD_HHMMSS_ffffff}.png
```

示例：

```text
pax1000_GetPAX1000Data_20260602_113141_123456.png
```

## 11. 兼容性与扩展记录

当前实现保留了 `parameter` 字段，但尚未使用。后续如需扩展，建议保持外层响应结构不变，仅在 `parameter` 或 `Value` 内增加字段。

建议新增 `opcode` 时遵循以下原则：

- 保持 `opcode` 为英文动词或动宾短语。
- 成功响应统一返回 `IsSuccessful=true` 和 `ErrorMessage="Null"`。
- 失败响应统一返回 `IsSuccessful=false`、`Value=""` 和明确的 `ErrorMessage`。
- 对需要耗时 OCR 的接口设置客户端超时时间，建议不少于 10 秒。

## 12. 安全说明

当前 TCP 服务不提供身份认证、加密、权限控制或请求来源校验。生产或实验室网络中使用时建议：

- 优先监听 `127.0.0.1`，由本机上位机或网关程序转发。
- 如需局域网访问，请限制防火墙入站来源 IP。
- 不要直接暴露到公网。
- 客户端应记录失败响应和截图路径，便于追踪 OCR 或窗口遮挡问题。


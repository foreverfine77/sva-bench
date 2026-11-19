# SVA Bench

通过 USB 或 LAN 控制 SIGLENT SVA，按 JSON 配方采一条频谱或零扫宽迹线，同时保存实际设置。支持离线模拟，也能接入 RadioScope 的有线发射实验。

![SVA Bench：从配方、扫频到归档](docs/media/sweep-flow.svg)

## 1. 安装

需要 Python 3.10 或更高版本。当前是 Alpha。

从 GitHub 克隆后，在工具包目录安装。克隆会同时下载配方和文档。

```powershell
git clone https://github.com/foreverfine77/sva-bench.git
Set-Location sva-bench
python -m pip install .
```

**下载 ZIP**时，从 [GitHub Releases](https://github.com/foreverfine77/sva-bench/releases) 下载带版本的工具包，解压到任意工作目录，进入包含 `pyproject.toml` 的目录后运行 `python -m pip install .`。配方在 `examples/`。

已有自己的配方时，也可以直接从 Git 安装固定版：

```powershell
python -m pip install "git+https://github.com/foreverfine77/sva-bench.git@v0.1.0-alpha.1"
```

## 2. 先跑一次离线扫谱

```powershell
sva identify --device fake
sva sweep --device fake --recipe examples/sa-tone.json --out captures/demo
```

![实际执行 Fake 单扫的终端输出](docs/media/fake-terminal.png)

`fake` 生成模拟迹线，`settings.json` 标记 `simulated: true`。重跑时换一个输出目录。

## 3. 连接实物

| 方式 | 设备参数 | 准备 |
| --- | --- | --- |
| USBTMC | `--device usb`，或完整 VISA 资源名 | 用仪器后面板 USB Device 口。安装系统 VISA 驱动，并在本目录执行 `python -m pip install ".[usb]"`。 |
| LAN | `--device <仪器 IP 或主机名>` | 确认仪器 SCPI TCP 5025 端口可达。 |

先确定信号源、输入功率和衰减，再从识别和读回开始：

```powershell
sva identify --device usb
sva inspect --device usb
sva sweep --device usb --recipe examples/sa-tone.json --out captures/tone
sva sweep --device usb --recipe examples/sa-zero-span.json --out captures/pulse
```

`identify` 和 `inspect` 只读。 `sweep` 会应用配方、验证仪器读回、采一条迹线，并将仪器留在单扫模式。JSON 配方里的频率单位是 Hz、电平是 dBm、衰减是 dB。零扫宽用 `span_hz: 0`，同时填写 `sweep_s`。

| 输出 | 内容 |
| --- | --- |
| `spectrum.csv` | 普通扫频的横轴是 `frequency_hz`，零扫宽是 `time_s`；纵轴是 `level_dbm`。 |
| `settings.json` | 仪器身份、请求设置、配置后和采集后读回、采集时间。 |
| `screen.bmp` | 只有加 `--screenshot` 才保存。 |

## 4. 接入 RadioScope

1. 用启动 RadioScope 和批次脚本的**同一个 Python 环境**安装 SVA Bench。走 USB 时，在工具包目录运行 `python -m pip install ".[usb]"`。
2. 回到 RadioScope 根目录。保持 Web 发射页连接，按本次实物链路完成预检。
3. 先离线看一组计划，再按 RadioScope 的链路说明运行实物批次。

```powershell
$env:PYTHONPATH = "host"
python tools/web_instrument_batch.py --sva-driver sva-bench --sva usb --check-setup
python tools/web_instrument_batch.py --sva-driver sva-bench --profile rates-lo --limit 1 --web-full-span
```

适配器负责仪器配置与采谱。RadioScope 负责波形、TX 启停、关断核验和矩阵。完整实验命令见 RadioScope 的 `docs/hardware/sva-experiment-commands.md`。

## 5. 代码从哪里看

| 模块 | 职责 |
| --- | --- |
| `_driver.py` | SCPI、USB/LAN 和离线仪器模拟。导出时取自 RadioScope 的共用驱动。 |
| `measurement.py` | 打开设备、应用配方、确认读回、采集迹线。 |
| `archive.py` | 导出 CSV 和设置 JSON。 |
| `cli.py` | 命令解析、串起单次采集与归档。 |

当前 Alpha 用 Fake 验证了安装、命令和 RadioScope 接入。此打包版本的 USB 与 LAN 实物复测待完成。VNA 的自动校准和 S 参数输出留待后续验证。

## 许可证

采用 [MIT 许可证](LICENSE)。使用、修改和再分发时保留原版权和许可声明。

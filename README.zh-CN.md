# NetSpeed Taskbar Widget

![Powered by](https://img.shields.io/badge/Powered%20by-OpenAI%20%26%20DeepSeek-412991?logo=openai&logoColor=white)

**Windows 任务栏** 显示实时网速（上传 / 下载）简约工具，基于`Python+PyQt6`开发，显示效果近似`macOS iStat Menus`

## ✨ 核心功能

- 实时显示**下载 / 上传**速度（每秒刷新）
- 自动嵌入 Windows 任务栏，紧贴托盘左侧
- 深色透明背景 + 白色文字，适配任务栏样式
- 右键菜单可退出
- **单实例**运行，重复启动会提示并退出
- 自动适配不同分辨率和缩放，支持命令自定义大小
- 监听任务栏变化（Explorer 重启、任务栏子窗口变动）并自动重定位

## 🖼️ 效果演示

<p align="center">
  <img src="demo/img1.png" width="300" alt="任务栏侧边停靠" style="margin-right:10px;">
  <img src="demo/img2.png" width="380" alt="申请taskbar区域避免覆盖或者阻挡主页"><br><br>
  <img src="demo/img3.png" width="680" alt="性能" style="margin-right:10px;">
</p>

## 🚀 快速开始

### 1. 克隆项目
```bash
git clone https://github.com/tokenlock/NetSpeed.git
cd NetSpeed
```

### 2. 环境要求

- **Windows 10** （依赖任务栏窗口结构，**Windows 11 未验证**）
- **Python 3.x**
- 仅 Windows 可用（依赖 `win32gui` / `win32con`）

### 3. 安装依赖
```bash
pip install -r requirements.txt
```

### 4. 运行

```bash
python netspeed.py
```


## 🧩 参数配置

### 命令行参数

| 参数 | 简写 | 默认值 | 说明 |
|------|------|--------|------|
| `--width` | `-w` | `148` | 挂件宽度（px）。传值后覆盖类中的 `WIDTH` 常量 |

示例：

```bash
# 使用默认宽度（148px）
python netspeed.py

# 自定义宽度为 120px
python netspeed.py -w 120
python netspeed.py --width 120
```

### 类常量

所有外观参数都在 `NetSpeedWidget` 类的顶部常量里，改完重启即可：

| 常量 | 默认值 | 说明 |
|------|--------|------|
| `WIDTH` | `148` | 挂件宽度（px） |
| `HEIGHT` | `40` | 挂件高度（px） |
| `TEXT_WIDTH` | `55` | 速度文字区域宽度（px） |
| `FONT` | `"Segoe UI"` | 字体 |
| `FONT_SIZE` | `9` | 字号 |
| `ICON_WIDTH` | `32` | 图标逻辑宽度（px） |
| `ICON_HEIGHT` | `44` | 图标逻辑高度（px） |

> 注意：`ICON_WIDTH/HEIGHT` 实际用于 `scaled()`，但 `setFixedSize()` 用的是它们的一半（`ICON_WIDTH//2, ICON_HEIGHT//2`），这是为了配合 `@2x` 高清图


## 📦 打包

### 1. 安装 PyInstaller

```bash
pip install pyinstaller
```

### 2. 执行打包脚本

```bash
build.bat
```

脚本会调用 `PyInstaller`，使用 `--onefile --noconsole` 打包，内嵌图标和 `resource` 目录，并在完成后自动清理 `.spec` 文件和 `build/` 目录

### 3. 输出

```
dist/NetSpeed.exe
```

双击 `dist/NetSpeed.exe` 即可运行, 也可在命令行带 `-w` 参数启动以自定义宽度

### 4. 可选：开机自动启动（Windows）

1. 按 `Win + R`，输入 `shell:startup`，回车
2. 把 `dist/NetSpeed.exe` 的快捷方式放进打开的文件夹里
3. 右键快捷方式 → 属性，在「目标」末尾追加参数（例如 `-w 120`）即可适配不同分辨率

## ⌨️ 工作原理

1. **找任务栏**
   `FindWindow("Shell_TrayWnd")` → 递归查找 `ReBarWindow32` → `MSTaskSwWClass`，以及 `TrayNotifyWnd`

2. **改窗口样式**
   去掉 `WS_POPUP` 等顶层窗口样式，加上 `WS_CHILD`，让它能作为子窗口

3. **设父窗口**
   `SetParent(hwnd, taskrebar)`，把 Qt 窗口挂到任务栏的 ReBar 下

4. **动态定位**
   - 计算挂件应出现在托盘左侧的屏幕坐标；
   - 同时把 `MSTaskSwWClass`（任务按钮区）**右边界左移 `WIDTH`**，为挂件腾出空间；
   - 用 `SetWindowPos` 把挂件放到该位置

5. **事件驱动监控**
   通过 `SetWinEventHook` 监听系统的窗口事件（`EVENT_OBJECT_LOCATIONCHANGE` / `CREATE` / `DESTROY` / `HIDE`），事件回调转发到 Qt 信号 `win_event`，槽函数 `on_win_event` 只做轻量过滤：
   - 若事件属于已记录的 taskbar 家族 HWND，且是位置变化 → 直接 `reposition_widget()`；
   - 若是未知 HWND 的创建/销毁 → 调用 `_revalidate_taskbar()`，重新查找 `Shell_TrayWnd`，处理 Explorer 重启或子窗口重建；
   - 用一个 **1 秒低频兜底定时器** `_fallback_tick()` 覆盖启动阶段（任务栏尚未就绪）和极端竞态，避免纯事件驱动漏事件

6. **防重入**
   `reposition_widget()` 里用 `_in_reposition` 标志，避免 `MoveWindow(taskbar_view)` 自身触发 `LOCATIONCHANGE` 造成递归

7. **退出时还原**
   `closeEvent` 里解除 WinEvent hook、停止定时器，并把任务按钮区宽度加回 `WIDTH`，恢复任务栏原状

## 📁 目录结构

```
netspeed/
├── netspeed.py            # 主程序
├── requirements.txt
├── README.md
├── build.bat              # 打包exe
└── resource/
    └── netspeed@2x.png    # 图标
```


## 📄 License

MIT License

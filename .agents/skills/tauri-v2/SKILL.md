---
description: Tauri v2 桌面应用开发。使用于：创建 Tauri 命令、配置 capabilities/permissions、设置 Python sidecar、处理前后端 IPC 通信、打包发布。遇到 invoke 报错、权限静默失败、sidecar 启动问题时必须加载此 skill。
---

# Tauri v2 开发指南（Windows + Python Sidecar）

## 核心架构

```
frontend (React/Vite)
    ↓ invoke() / fetch()
Tauri 核心（Rust）
    ├── Rust commands（src-tauri/src/lib.rs）
    └── sidecar 进程（Python FastAPI）
            ↓ HTTP 127.0.0.1:PORT
```

## 项目结构

```
src-tauri/
├── src/
│   ├── lib.rs          # 命令定义 + app builder（移动端必须）
│   └── main.rs         # 桌面入口，仅调用 lib.rs
├── binaries/           # sidecar 可执行文件放这里
│   └── lumen-backend-x86_64-pc-windows-msvc.exe
├── capabilities/
│   └── default.json    # 权限配置（必须显式授权）
├── icons/
├── build.rs
├── Cargo.toml
└── tauri.conf.json
```

## Windows 环境依赖

必须安装，缺一不可：
1. Microsoft C++ Build Tools（勾选"Desktop development with C++"）
2. WebView2 Runtime（Windows 10 1803+ 和 Windows 11 已内置）
3. Rust（务必选 `x86_64-pc-windows-msvc` 工具链，不能用 GNU）
4. Node.js 20+

验证 Rust 工具链：
```bash
rustup show  # 确认 default host 是 msvc 不是 gnu
```

## Rust 命令定义

### lib.rs 标准结构（移动端兼容写法）

```rust
// src-tauri/src/lib.rs
#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}!", name)
}

#[tauri::command]
async fn fetch_data(app: tauri::AppHandle) -> Result<String, String> {
    // 异步命令必须返回 Result
    Ok("data".to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())  // sidecar 需要
        .invoke_handler(tauri::generate_handler![
            greet,
            fetch_data,
            // 每个命令都必须在这里注册，漏掉会静默失败
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
```

```rust
// src-tauri/src/main.rs
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
fn main() { app_lib::run(); }
```

### 常见错误：invoke_handler 漏注册命令

症状：前端 invoke 报 `command not found`，但 Rust 编译没有报错。
原因：命令写了但没加到 `generate_handler![]` 里。
修复：每次新增命令后立即更新 `generate_handler!`。

## Capabilities 与 Permissions（最常踩坑）

Tauri v2 默认拒绝所有插件命令，必须在 capabilities 中显式授权。

### 基础结构

```json
// src-tauri/capabilities/default.json
{
  "$schema": "../gen/schemas/desktop-schema.json",
  "identifier": "default",
  "description": "Default capability",
  "windows": ["main"],
  "permissions": [
    "core:default",
    "shell:allow-execute",
    "shell:allow-spawn"
  ]
}
```

### Sidecar 权限（必须添加）

```json
"permissions": [
  "core:default",
  "shell:allow-execute",
  "shell:allow-spawn"
]
```

### 常见权限列表

| 功能 | 权限标识符 |
|------|-----------|
| Sidecar 执行 | `shell:allow-execute` |
| Sidecar spawn | `shell:allow-spawn` |
| 读取文件 | `fs:allow-read-text-file` |
| 写入文件 | `fs:allow-write-text-file` |
| 系统托盘 | `core:tray:default` |
| 通知 | `notification:default` |
| 打开对话框 | `dialog:allow-open` |

权限静默失败的症状：前端 invoke 被 reject，错误信息包含 `not allowed`。
修复：查看错误信息中 `Permissions associated with this command` 列出的权限，添加对应项。

## Python Sidecar 配置

### 步骤一：打包 Python 后端

```bash
# 安装 PyInstaller
pip install pyinstaller

# 打包 FastAPI 后端为单文件可执行
pyinstaller --onefile --name lumen-backend app/backend/main.py

# 输出在 dist/lumen-backend.exe
```

### 步骤二：重命名二进制文件

Tauri 要求二进制文件名包含目标平台的 target triple 后缀：

```bash
# 查看当前平台 target triple
rustc -Vv | grep host

# Windows x64 结果示例：host: x86_64-pc-windows-msvc
# 重命名：
# lumen-backend.exe → lumen-backend-x86_64-pc-windows-msvc.exe
```

将重命名后的文件放入 `src-tauri/binaries/`。

### 步骤三：tauri.conf.json 配置

```json
{
  "bundle": {
    "externalBin": [
      "binaries/lumen-backend"
    ]
  }
}
```

路径相对于 `src-tauri/`，不要带平台后缀，Tauri 自动匹配。

### 步骤四：Rust 中启动 sidecar

```rust
use tauri_plugin_shell::ShellExt;

#[tauri::command]
async fn start_backend(app: tauri::AppHandle) -> Result<(), String> {
    let sidecar_command = app.shell()
        .sidecar("lumen-backend")
        .map_err(|e| e.to_string())?;

    let (_rx, _child) = sidecar_command
        .spawn()
        .map_err(|e| e.to_string())?;

    Ok(())
}
```

或在 app setup 中自动启动：

```rust
tauri::Builder::default()
    .setup(|app| {
        let handle = app.handle().clone();
        tauri::async_runtime::spawn(async move {
            let sidecar = handle.shell()
                .sidecar("lumen-backend")
                .unwrap();
            sidecar.spawn().unwrap();
        });
        Ok(())
    })
```

### 开发阶段不需要打包 sidecar

开发时直接在终端运行 `uvicorn`，前端请求正常打到 `127.0.0.1:8000`。
只有 `tauri build` 发布时才需要 sidecar 可执行文件。

## tauri.conf.json 关键配置

```json
{
  "productName": "Lumen",
  "version": "0.1.0",
  "identifier": "com.lumen.app",
  "build": {
    "frontendDist": "../app/frontend/dist",
    "devUrl": "http://localhost:5173",
    "beforeDevCommand": "cd app/frontend && npm run dev",
    "beforeBuildCommand": "cd app/frontend && npm run build"
  },
  "app": {
    "windows": [
      {
        "label": "main",
        "title": "Lumen",
        "width": 1100,
        "height": 750,
        "minWidth": 800,
        "minHeight": 600
      }
    ]
  },
  "bundle": {
    "active": true,
    "targets": "all",
    "externalBin": ["binaries/lumen-backend"],
    "icon": ["icons/icon.png"]
  }
}
```

## 前端 invoke 调用

```typescript
import { invoke } from '@tauri-apps/api/core';

// 调用 Rust 命令
const result = await invoke<string>('greet', { name: 'Lumen' });

// Rust 函数名 snake_case → invoke 时也用 snake_case
// Rust 参数名 snake_case → JS 传参时用 camelCase
// 例：fn fetch_data(user_id: u32) → invoke('fetch_data', { userId: 1 })
```

## 系统托盘（常用功能）

```rust
// Cargo.toml 添加
// tauri = { features = ["tray-icon"] }

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
};

.setup(|app| {
    let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&quit])?;

    TrayIconBuilder::new()
        .icon(app.default_window_icon().unwrap().clone())
        .menu(&menu)
        .on_menu_event(|app, event| {
            if event.id == "quit" {
                app.exit(0);
            }
        })
        .build(app)?;
    Ok(())
})
```

tauri.conf.json 中需添加：
```json
"app": {
  "trayIcon": {
    "iconPath": "icons/icon.png"
  }
}
```

## 调试

- 前端：开发模式下按 `F12` 打开 DevTools（完整 Chrome DevTools）
- Rust：`println!` 输出在终端，`eprintln!` 输出到 stderr
- 开发命令：`pnpm tauri dev`（或 npm/yarn）
- 生产构建：`pnpm tauri build`

## v1 → v2 常见变化（避免用错旧 API）

| v1 | v2 |
|----|-----|
| `tauri::command` 直接可用 | 需要在 capabilities 中授权 |
| `app.shell().command()` | `app.shell().sidecar()` 专用于 sidecar |
| `@tauri-apps/api/shell` | `@tauri-apps/plugin-shell` |
| `@tauri-apps/api/fs` | `@tauri-apps/plugin-fs` |
| 权限默认开放 | 权限默认全部关闭 |

## Sidecar stdout 流式监控（重要）

sidecar 启动后可以监听其 stdout/stderr，并通过事件系统实时推送到前端：

```rust
use tauri_plugin_shell::ShellExt;
use tauri_plugin_shell::process::CommandEvent;
use tauri::Emitter;

let sidecar_command = app.shell().sidecar("lumen-backend").unwrap();
let (mut rx, child) = sidecar_command.spawn().expect("Failed to spawn");

// 把 child 存起来用于后续 kill
app.manage(BackendProcess(Mutex::new(Some(child))));

// 异步消费 stdout 事件
tauri::async_runtime::spawn(async move {
    while let Some(event) = rx.recv().await {
        match event {
            CommandEvent::Stdout(line) => {
                // line 是 Vec<u8>，转 String 后推送到前端
                let text = String::from_utf8_lossy(&line);
                app.emit("backend:stdout", text.to_string()).ok();
            }
            CommandEvent::Stderr(line) => {
                let text = String::from_utf8_lossy(&line);
                app.emit("backend:stderr", text.to_string()).ok();
            }
            CommandEvent::Terminated(status) => {
                app.emit("backend:terminated", status.code).ok();
            }
            _ => {}
        }
    }
});
```

前端监听：
```typescript
import { listen } from '@tauri-apps/api/event';
listen<string>('backend:stdout', (e) => console.log('[backend]', e.payload));
```

## Sidecar 参数校验（安全必须）

`shell:allow-execute` 可配置 regex validator 限制参数：

```json
{
  "identifier": "shell:allow-execute",
  "allow": [{
    "name": "binaries/lumen-backend",
    "sidecar": true,
    "args": [
      "--port",
      { "validator": "\\d{4,5}" },
      "--host",
      { "validator": "\\S+" }
    ]
  }]
}
```

## 事件系统详解（Rust → 前端）

### 全局事件 vs Webview 事件

```rust
use tauri::{Emitter, EventTarget};

// 全局：所有窗口都收到
app.emit("global-event", payload).unwrap();

// 定向：只发给特定窗口
app.emit_to("main", "window-event", payload).unwrap();

// 过滤：发给匹配条件的窗口
app.emit_filter("filtered-event", payload, |target| {
    matches!(target, EventTarget::WebviewWindow { label } if label == "main" || label == "settings")
}).unwrap();
```

### 结构化事件负载

```rust
#[derive(Clone, serde::Serialize)]
#[serde(rename_all = "camelCase")]
struct BackendStatus {
    port: u16,
    ready: bool,
    uptime_secs: u64,
}

app.emit("backend:status", BackendStatus { port: 8000, ready: true, uptime_secs: 42 }).ok();
```

### Channel API（流式数据首选，优于事件）

当需要高频、有序的数据推送时，用 Channel 而非事件：

```rust
use tauri::ipc::Channel;

#[tauri::command]
fn stream_download(app: AppHandle, url: String, on_event: Channel<ProgressEvent>) {
    for progress in [0, 25, 50, 75, 100] {
        on_event.send(ProgressEvent { url: url.clone(), progress }).ok();
    }
}
```

前端：
```typescript
import { invoke, Channel } from '@tauri-apps/api/core';

const onEvent = new Channel<ProgressEvent>();
onEvent.onmessage = (msg) => console.log(`${msg.progress}%`);
await invoke('stream_download', { url: '...', onEvent });
```

## 从 Rust 调用前端的三种方式

| 方式 | 场景 | 特点 |
|------|------|------|
| `app.emit()` | 推送通知、状态变更 | 简单，JSON 序列化，支持多消费者 |
| `Channel` | 流式数据、进度 | 有序保证，低延迟，高吞吐 |
| `webview.eval()` | 需要执行任意 JS | 直接 eval，适合简单调用 |

```rust
// 方式三：直接执行 JS
let webview = app.get_webview_window("main").unwrap();
webview.eval("console.log('hello from Rust')").ok();
// 带参数用 serialize-to-javascript crate
```

## 启动画面（Splashscreen）

核心思路：main 窗口初始隐藏，splashscreen 窗口显示，等前后端初始化完成再切换。

### 1. 注册两个窗口

```json
// tauri.conf.json
{
  "app": {
    "windows": [
      { "label": "main", "visible": false, "width": 1100, "height": 750 },
      { "label": "splashscreen", "url": "/splashscreen", "width": 400, "height": 300, "decorations": false }
    ]
  }
}
```

### 2. 追踪初始化状态

```rust
use std::sync::Mutex;
use tauri::Manager;

struct SetupState {
    frontend_ready: bool,
    backend_ready: bool,
}

#[tauri::command]
async fn set_ready(app: AppHandle, state: State<'_, Mutex<SetupState>>, task: String) -> Result<(), ()> {
    let mut lock = state.lock().unwrap();
    if task == "frontend" { lock.frontend_ready = true; }
    if task == "backend" { lock.backend_ready = true; }

    if lock.frontend_ready && lock.backend_ready {
        app.get_webview_window("splashscreen").unwrap().close().ok();
        app.get_webview_window("main").unwrap().show().ok();
    }
    Ok(())
}
```

### 3. setup 阶段启动后台任务

```rust
.setup(|app| {
    app.manage(Mutex::new(SetupState { frontend_ready: false, backend_ready: false }));

    // 异步启动 backend 初始化
    let handle = app.handle().clone();
    tauri::async_runtime::spawn(async move {
        // 等待 Python 后端 /health 返回 OK...
        set_ready(handle.clone(), handle.state(), "backend".into()).await.ok();
    });

    Ok(())
})
```

前端 `splashscreen` 页面完成后调用 `invoke('set_ready', { task: 'frontend' })`。

### 关于是否需要启动画面

官方建议：尽量不用启动画面，直接显示主窗口 + 角落 loading 指示器更好。只有确实必须等某些初始化任务完成的场景才用。Lumen 的 Python 后端冷启动需要几秒，属于合理场景。

## 窗口关闭到托盘（核心桌面体验）

拦截 `CloseRequested`，隐藏窗口而非退出：

```rust
// Cargo.toml: tauri = { features = ["tray-icon"] }

use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager,
};

// 托盘菜单
let show = MenuItem::with_id(app, "show", "显示", true, None::<&str>)?;
let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
let menu = Menu::with_items(app, &[&show, &quit])?;

let _tray = TrayIconBuilder::new()
    .icon(app.default_window_icon().unwrap().clone())
    .menu(&menu)
    .on_menu_event(|app, event| match event.id.as_ref() {
        "show" => {
            if let Some(w) = app.get_webview_window("main") {
                w.show().ok(); w.set_focus().ok();
            }
        }
        "quit" => { app.exit(0); }
        _ => {}
    })
    .on_tray_icon_event(|tray, event| {
        // 双击托盘图标恢复窗口
        if let TrayIconEvent::DoubleClick { button: MouseButton::Left, .. } = event {
            let app = tray.app_handle();
            if let Some(w) = app.get_webview_window("main") {
                w.show().ok(); w.set_focus().ok();
            }
        }
    })
    .build(app)?;
```

拦截关闭事件：

```rust
// 在 .on_window_event 中处理
let window_clone = window.clone();
window.on_window_event(move |event| {
    if let WindowEvent::CloseRequested { api, .. } = event {
        api.prevent_close();  // 阻止退出
        window_clone.hide().ok();  // 隐藏到托盘
    }
});
```

## 单实例限制

防止用户重复启动。

```toml
# Cargo.toml
tauri-plugin-single-instance = "2"
```

```json
// capabilities
"single-instance:default"
```

```rust
// lib.rs
.plugin(tauri_plugin_single_instance::init(|app, args, cwd| {
    // 第二个实例启动时，聚焦已有窗口
    if let Some(w) = app.get_webview_window("main") {
        w.show().ok(); w.set_focus().ok();
    }
}))
```

## 应用数据路径（不要硬编码 ~/.lumen/）

打包后环境变量不可靠，用 Tauri 的路径 API：

```rust
use tauri::Manager;

// 获取平台标准数据目录
// Windows: C:\Users\<user>\AppData\Roaming\com.lumen.app
// macOS:   ~/Library/Application Support/com.lumen.app
// Linux:   ~/.local/share/com.lumen.app
let data_dir = app.path().app_data_dir().expect("no data dir");

// 传给 Python 后端做命令行参数
python_cmd.arg("--data-dir").arg(data_dir.to_string_lossy());
```

Python 端接收：
```python
# backend/config.py
import argparse, sys
parser = argparse.ArgumentParser()
parser.add_argument("--data-dir", default=None)
args, _ = parser.parse_known_args()

USER_DATA_DIR = Path(args.data_dir) if args.data_dir else Path.home() / ".lumen"
```

前端也可以用 `@tauri-apps/api/path` 获取标准路径：
```typescript
import { appDataDir } from '@tauri-apps/api/path';
const dir = await appDataDir(); // ~/.local/share/com.lumen.app/
```

## 文件拖拽导入

```rust
// lib.rs
.on_window_event(|window, event| {
    if let WindowEvent::DragDrop(drop_event) = event {
        match drop_event {
            tauri::DragDropEvent::Drop { paths, .. } => {
                for path in paths {
                    window.emit("file:dropped", path.to_string_lossy()).ok();
                }
            }
            _ => {}
        }
    }
})
```

需要 `drag-drop:default` 权限。

## 原生通知

```toml
# Cargo.toml
tauri-plugin-notification = "2"
```

```json
// capabilities
"notification:default"
```

```rust
use tauri_plugin_notification::NotificationExt;

app.notification()
    .builder()
    .title("Lumen")
    .body("后台记忆审查完成，发现 3 条新信息")
    .show()
    .ok();
```

## 窗口状态持久化

记住窗口位置和大小：

```toml
tauri-plugin-window-state = "2"
```

```rust
.plugin(tauri_plugin_window_state::Builder::default().build())
```

自动保存/恢复窗口位置、大小、最大化状态，无需手动处理。

## 持久键值存储（Store）

替代 localStorage，存配置到磁盘：

```toml
tauri-plugin-store = "2"
```

```rust
use tauri_plugin_store::StoreExt;

let store = app.store("settings.json").unwrap();
store.set("theme", json!("dark"));
```

前端：
```typescript
import { Store } from '@tauri-apps/plugin-store';
const store = await Store.load('settings.json');
await store.set('theme', 'dark');
await store.save();
```

## 应用内自动更新

```toml
tauri-plugin-updater = "2"
```

```json
// tauri.conf.json
"plugins": {
  "updater": {
    "endpoints": ["https://cdn.example.com/updates/{{target}}/{{arch}}/{{current_version}}"],
    "pubkey": "YOUR_PUBLIC_KEY"
  }
}
```

## 开机自启

```toml
tauri-plugin-autostart = "2"
```

```rust
use tauri_plugin_autostart::MacosLauncher;

.plugin(tauri_plugin_autostart::init(
    MacosLauncher::LaunchAgent,
    Some(vec!["--minimized"]),
))
```

## 版本控制注意

- **必须提交** `src-tauri/Cargo.lock`（确定性构建）
- **不要提交** `src-tauri/target/`
- 可在 `src-tauri/` 下创建 `.taurignore` 控制监视范围

## 常用插件速查表

| 插件 | Cargo crate | 用途 |
|------|-----------|------|
| single-instance | `tauri-plugin-single-instance` | 防重复启动 |
| window-state | `tauri-plugin-window-state` | 记住窗口位置 |
| notification | `tauri-plugin-notification` | 系统通知 |
| store | `tauri-plugin-store` | 键值持久存储 |
| updater | `tauri-plugin-updater` | 自动更新 |
| autostart | `tauri-plugin-autostart` | 开机自启 |
| dialog | `tauri-plugin-dialog` | 文件对话框 |
| fs | `tauri-plugin-fs` | 文件系统 |
| shell | `tauri-plugin-shell` | sidecar/shell |
| clipboard | `tauri-plugin-clipboard` | 剪贴板 |
| global-shortcut | `tauri-plugin-global-shortcut` | 全局快捷键 |
| process | `tauri-plugin-process` | 进程信息 |
| os-info | `tauri-plugin-os-info` | 系统信息 |

## Windows 打包注意事项

- 生成 `.exe` 安装包需要 NSIS，`.msi` 需要 WiX Toolset
- 代码签名不是必须的，但没有签名 Windows Defender 可能弹警告
- `targets: "nsis"` 生成安装向导，`targets: "msi"` 生成 MSI 包
- 打包命令：`pnpm tauri build`
- 打包前用 PyInstaller 把 Python 后端打成 sidecar 二进制

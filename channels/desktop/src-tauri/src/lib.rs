use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
use tauri::{Emitter, Manager, WindowEvent};

// ── Windows Job Object (ensures child processes die with parent) ──

#[cfg(windows)]
mod windows_process {
    use std::os::windows::io::AsRawHandle;
    use std::process::Child;
    use windows::Win32::Foundation::{CloseHandle, HANDLE};
    use windows::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    pub struct JobObject(HANDLE);

    // SAFETY: HANDLE is an opaque pointer value; Windows APIs are thread-safe with handles
    unsafe impl Send for JobObject {}
    unsafe impl Sync for JobObject {}

    impl JobObject {
        pub fn new() -> Result<Self, windows::core::Error> {
            unsafe {
                let handle = CreateJobObjectW(None, None)?;
                let mut info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as *const _,
                    std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                )?;
                Ok(Self(handle))
            }
        }

        pub fn assign_process(&self, child: &Child) -> Result<(), windows::core::Error> {
            unsafe { AssignProcessToJobObject(self.0, HANDLE(child.as_raw_handle())) }
        }
    }

    impl Drop for JobObject {
        fn drop(&mut self) {
            unsafe {
                let _ = CloseHandle(self.0);
            }
        }
    }
}

#[cfg(windows)]
static JOB_OBJECT: std::sync::OnceLock<windows_process::JobObject> = std::sync::OnceLock::new();

struct PythonBackend {
    child: Mutex<Option<Child>>,
    started: Mutex<bool>,
}

/// Project root = three levels up from src-tauri (channels/desktop/src-tauri → repo root).
fn project_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()  // channels/desktop
        .and_then(|p| p.parent())  // channels
        .and_then(|p| p.parent())  // repo root
        .expect("could not resolve project root")
        .to_path_buf()
}

/// Check if a port is in use by trying to bind to it.
fn is_port_in_use(port: u16) -> bool {
    std::net::TcpListener::bind(format!("127.0.0.1:{}", port)).is_err()
}

/// Kill any process listening on the given port (Windows).
#[cfg(windows)]
fn kill_process_on_port(port: u16) {
    log::info!("Checking for processes on port {}...", port);

    let output = Command::new("cmd")
        .args(["/c", &format!("netstat -ano | findstr :{}", port)])
        .output();

    if let Ok(out) = output {
        let text = String::from_utf8_lossy(&out.stdout);
        let mut killed = false;
        for line in text.lines() {
            // Format: TCP    127.0.0.1:8000    0.0.0.0:0    LISTENING    12345
            if let Some(pid_str) = line.trim().split_whitespace().last() {
                if let Ok(pid) = pid_str.parse::<u32>() {
                    log::info!("Killing orphaned process {} on port {}", pid, port);
                    let result = Command::new("taskkill")
                        .args(["/F", "/T", "/PID", &pid.to_string()])
                        .output();
                    if result.is_ok() {
                        killed = true;
                    }
                }
            }
        }
        if killed {
            // Give the OS a moment to release the port
            std::thread::sleep(std::time::Duration::from_millis(500));
        }
    }
}

#[cfg(not(windows))]
fn kill_process_on_port(_port: u16) {
    // TODO: implement for other platforms if needed
}

/// Start the Python FastAPI backend as a sidecar process.
fn start_backend() -> Option<Child> {
    let port = 8000u16;

    // Check for port conflicts and kill orphaned processes
    if is_port_in_use(port) {
        log::warn!(
            "Port {} is already in use, attempting to free it...",
            port
        );
        kill_process_on_port(port);

        if is_port_in_use(port) {
            log::error!(
                "Port {} is still in use after cleanup attempt; backend may fail to start",
                port
            );
            // We still try to start — uvicorn will fail fast and the user sees the error
        }
    }

    let python = if cfg!(target_os = "windows") {
        "python"
    } else {
        "python3"
    };

    match Command::new(python)
        .current_dir(project_root())
        .args([
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--log-level",
            "warning",
        ])
        .spawn()
    {
        Ok(child) => {
            #[cfg(windows)]
            if let Some(job) = JOB_OBJECT.get() {
                if let Err(e) = job.assign_process(&child) {
                    log::warn!("Failed to assign process to job object: {}", e);
                } else {
                    log::info!("Python process assigned to job object");
                }
            }
            log::info!("Python backend started (pid: {})", child.id());
            Some(child)
        }
        Err(e) => {
            log::error!("Failed to start Python backend: {}", e);
            None
        }
    }
}

/// Kill the Python backend process and ensure the entire process tree is dead.
fn stop_backend(child: &mut Option<Child>) {
    if let Some(ref mut c) = child {
        let pid = c.id();

        let _ = c.kill();
        let _ = c.wait();

        // On Windows, ensure the entire process tree is dead (covers uvicorn workers)
        #[cfg(windows)]
        {
            log::info!("Ensuring process tree for PID {} is terminated", pid);
            let _ = Command::new("taskkill")
                .args(["/F", "/T", "/PID", &pid.to_string()])
                .output();
        }

        log::info!("Python backend stopped");
    }
}

// ── Tauri Commands ──

#[tauri::command]
fn read_file_content(path: String) -> Result<String, String> {
    std::fs::read_to_string(&path).map_err(|e| format!("Failed to read {}: {}", path, e))
}

#[tauri::command]
fn file_exists(path: String) -> bool {
    std::path::Path::new(&path).exists()
}

#[tauri::command]
fn is_backend_running(state: tauri::State<PythonBackend>) -> bool {
    *state.started.lock().unwrap()
}

#[tauri::command]
fn restart_backend(state: tauri::State<PythonBackend>) -> Result<(), String> {
    let mut child = state.child.lock().unwrap();
    stop_backend(&mut child);
    *child = start_backend();
    Ok(())
}

#[tauri::command]
fn start_folder_watch(path: String, app: tauri::AppHandle) -> Result<(), String> {
    use notify::{Config, Event, EventKind, RecommendedWatcher, RecursiveMode, Watcher};
    use std::path::PathBuf;

    let watch_path = PathBuf::from(&path);
    if !watch_path.exists() {
        return Err(format!("Path does not exist: {}", path));
    }

    let (tx, mut rx) = tokio::sync::mpsc::channel(32);

    let mut watcher = RecommendedWatcher::new(
        move |res: Result<Event, notify::Error>| {
            if let Ok(event) = res {
                match event.kind {
                    EventKind::Create(_) | EventKind::Modify(_) => {
                        for p in &event.paths {
                            let _ = tx.blocking_send(p.to_string_lossy().to_string());
                        }
                    }
                    _ => {}
                }
            }
        },
        Config::default(),
    )
    .map_err(|e| e.to_string())?;

    watcher
        .watch(&watch_path, RecursiveMode::NonRecursive)
        .map_err(|e| e.to_string())?;

    // Spawn async handler that emits events to frontend
    tauri::async_runtime::spawn(async move {
        while let Some(file_path) = rx.recv().await {
            let _ = app.emit("folder:change", file_path);
        }
        drop(watcher);
    });

    log::info!("Started watching folder: {}", path);
    Ok(())
}

/// Open a file or folder in the OS file manager / default application.
#[tauri::command]
fn open_path(path: String) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        Command::new("explorer")
            .arg(&path)
            .spawn()
            .map_err(|e| format!("Failed to open {}: {}", path, e))?;
    }
    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .arg(&path)
            .spawn()
            .map_err(|e| format!("Failed to open {}: {}", path, e))?;
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        Command::new("xdg-open")
            .arg(&path)
            .spawn()
            .map_err(|e| format!("Failed to open {}: {}", path, e))?;
    }
    Ok(())
}

// ── Application Entry ──

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // Configure logging in debug mode
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            // Create Windows Job Object so child processes die with parent
            #[cfg(windows)]
            {
                match windows_process::JobObject::new() {
                    Ok(job) => {
                        let _ = JOB_OBJECT.set(job);
                        log::info!("Windows job object created (kill-on-close)");
                    }
                    Err(e) => {
                        log::warn!("Failed to create job object: {}", e);
                    }
                }
            }

            // Start Python backend
            let child = start_backend();
            let started = child.is_some();

            app.manage(PythonBackend {
                child: Mutex::new(child),
                started: Mutex::new(started),
            });

            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::Destroyed = event {
                let state = window.state::<PythonBackend>();
                let mut child = state.child.lock().unwrap();
                stop_backend(&mut child);
            }
        })
        .invoke_handler(tauri::generate_handler![
            read_file_content,
            file_exists,
            is_backend_running,
            restart_backend,
            start_folder_watch,
            open_path,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

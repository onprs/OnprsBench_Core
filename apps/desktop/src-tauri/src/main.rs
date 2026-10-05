// 桌面壳入口。Python sidecar 的启动由开发脚本/安装包负责（见 scripts/dev.mjs）。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

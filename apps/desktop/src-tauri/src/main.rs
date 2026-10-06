// 桌面壳入口。Python sidecar 的启动由开发脚本/安装包负责（见 scripts/dev.mjs）。
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

/// 同步窗口 DWM 边框色到当前主题（Windows 11+；旧系统忽略失败）。
#[tauri::command]
fn set_window_border_color(window: tauri::Window, color: String) -> Result<(), String> {
    set_border_color_impl(&window, &color)
}

#[cfg(windows)]
fn set_border_color_impl(window: &tauri::Window, color: &str) -> Result<(), String> {
    use windows::Win32::Graphics::Dwm::{DwmSetWindowAttribute, DWMWA_BORDER_COLOR};

    let hwnd = window.hwnd().map_err(|e| e.to_string())?;
    let value = parse_colorref(color)?;
    unsafe {
        DwmSetWindowAttribute(
            hwnd,
            DWMWA_BORDER_COLOR,
            &value as *const u32 as *const core::ffi::c_void,
            std::mem::size_of::<u32>() as u32,
        )
        .map_err(|e| e.to_string())
    }
}

#[cfg(not(windows))]
fn set_border_color_impl(_window: &tauri::Window, _color: &str) -> Result<(), String> {
    Ok(())
}

/// "#RRGGBB" → COLORREF（0x00BBGGRR）。
#[cfg(windows)]
fn parse_colorref(color: &str) -> Result<u32, String> {
    let value = u32::from_str_radix(color.trim().trim_start_matches('#'), 16)
        .map_err(|e| format!("非法颜色值 {color}: {e}"))?;
    let r = (value >> 16) & 0xFF;
    let g = (value >> 8) & 0xFF;
    let b = value & 0xFF;
    Ok((b << 16) | (g << 8) | r)
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![set_window_border_color])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

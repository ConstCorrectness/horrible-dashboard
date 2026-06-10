// The Tauri shell stays thin: app logic lives in the backend and packages/.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    tauri::Builder::default()
        .run(tauri::generate_context!())
        .expect("error while running horrible-dashboard");
}

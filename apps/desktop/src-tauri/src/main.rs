// The Tauri shell stays thin: app logic lives in the backend and packages/.
// Its one real job is supervising the backend process — see backend.rs.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;

use std::sync::Arc;

use tauri::Manager;

fn main() {
    #[cfg(target_os = "linux")]
    {
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
        std::env::set_var("LIBGL_ALWAYS_SOFTWARE", "1");
        std::env::set_var("WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS", "1");
        std::env::set_var("WEBKIT_DISABLE_COMPOSITING_MODE", "1");
    }

    let supervisor = Arc::new(backend::BackendSupervisor::new());
    backend::start(Arc::clone(&supervisor));

    tauri::Builder::default()
        .manage(supervisor)
        .invoke_handler(tauri::generate_handler![backend::backend_status])
        .build(tauri::generate_context!())
        .expect("error while building horrible-dashboard")
        .run(|app, event| {
            if matches!(
                event,
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
            ) {
                // Idempotent: may fire for both events.
                app.state::<Arc<backend::BackendSupervisor>>().shutdown();
            }
        });
}

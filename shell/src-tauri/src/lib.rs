// Learn more about Tauri commands at https://tauri.app/develop/calling-rust/
use enigo::{Enigo, Keyboard, Settings};
use tauri::{Emitter, Manager};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

#[tauri::command]
fn greet(name: &str) -> String {
    format!("Hello, {}! You've been greeted from Rust!", name)
}

// 5e-i (proven working): injects text via simulated keystrokes into
// whatever window currently has OS focus — the mechanism 5e-ii's real
// dictation flow depends on.
#[tauri::command]
fn inject_text(text: String) -> Result<(), String> {
    let mut enigo = Enigo::new(&Settings::default()).map_err(|e| e.to_string())?;
    enigo.text(&text).map_err(|e| e.to_string())?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    // Ctrl+Shift+Space: show/hide the main window.
    let toggle_shortcut = Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::Space);

    // Ctrl+Space: push-to-talk dictation. Press starts recording (shows the
    // indicator window + tells the main window's JS to start capturing mic
    // audio via MediaRecorder — reusing the exact pipeline proven in 5d
    // rather than adding a separate native audio-capture crate). Release
    // stops recording, hides the indicator, and the JS side takes it from
    // there (transcribe -> inject_text).
    //
    // Originally Alt+Space, rejected: it's a native Windows-reserved combo
    // (opens the window system menu), and a standalone Alt press has its
    // own OS meaning (menu-bar focus). Real key-press timing isn't
    // perfectly simultaneous, so Windows would sometimes catch a bare Alt
    // moment before Space joined it, stealing focus mid-recording and
    // producing garbled, cut-off transcripts. Ctrl+Space doesn't have
    // either problem.
    let dictation_shortcut = Shortcut::new(Some(Modifiers::CONTROL), Code::Space);

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(move |app, shortcut, event| {
                    if shortcut == &toggle_shortcut && event.state() == ShortcutState::Pressed {
                        if let Some(window) = app.get_webview_window("main") {
                            match window.is_visible() {
                                Ok(true) => {
                                    let _ = window.hide();
                                }
                                _ => {
                                    let _ = window.show();
                                    let _ = window.set_focus();
                                }
                            }
                        }
                    } else if shortcut == &dictation_shortcut {
                        match event.state() {
                            ShortcutState::Pressed => {
                                if let Some(indicator) = app.get_webview_window("indicator") {
                                    let _ = indicator.show();
                                }
                                let _ = app.emit("start-dictation", ());
                            }
                            ShortcutState::Released => {
                                if let Some(indicator) = app.get_webview_window("indicator") {
                                    let _ = indicator.hide();
                                }
                                let _ = app.emit("stop-dictation", ());
                            }
                        }
                    }
                })
                .build(),
        )
        .setup(move |app| {
            app.global_shortcut().register(toggle_shortcut)?;
            app.global_shortcut().register(dictation_shortcut)?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![greet, inject_text])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

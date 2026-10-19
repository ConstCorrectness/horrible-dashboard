//! Reading and writing the system clipboard, by asking the OS's own tool.
//!
//! No clipboard crate, for the reason `console.rs` gives: the ones that exist
//! pull an X11/Wayland stack in on Linux and own a thread for the life of the
//! process. The two things that want a clipboard here — pasting and copying a
//! crosshair share code — are rare, user-initiated, and a short ASCII string, so
//! a child process is plenty. It is still a few hundred milliseconds on Windows
//! (PowerShell starting), which is why `paste_async` exists: a frame must never
//! wait on it.

use std::io::Write;
use std::process::{Command, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::thread;

fn command(program: &str) -> Command {
    #[allow(unused_mut)]
    let mut cmd = Command::new(program);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // CREATE_NO_WINDOW: no console flashing up over a fullscreen game.
        cmd.creation_flags(0x0800_0000);
    }
    cmd
}

/// The clipboard's text, or why it could not be read.
pub fn read_text() -> Result<String, String> {
    let attempts: &[(&str, &[&str])] = if cfg!(windows) {
        &[(
            "powershell",
            &[
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-Clipboard -Raw",
            ],
        )]
    } else if cfg!(target_os = "macos") {
        &[("pbpaste", &[])]
    } else {
        &[
            ("wl-paste", &["--no-newline"]),
            ("xclip", &["-selection", "clipboard", "-o"]),
            ("xsel", &["--clipboard", "--output"]),
        ]
    };
    let mut last = String::from("no clipboard tool");
    for (program, args) in attempts {
        match command(program).args(*args).stderr(Stdio::null()).output() {
            Ok(out) if out.status.success() => {
                return Ok(String::from_utf8_lossy(&out.stdout).into_owned());
            }
            Ok(out) => last = format!("{program} exited with {}", out.status),
            Err(e) => last = format!("{program}: {e}"),
        }
    }
    Err(last)
}

/// Put `text` on the clipboard.
pub fn write_text(text: &str) -> Result<(), String> {
    let attempts: &[(&str, &[&str])] = if cfg!(windows) {
        &[("clip", &[])]
    } else if cfg!(target_os = "macos") {
        &[("pbcopy", &[])]
    } else {
        &[
            ("wl-copy", &[]),
            ("xclip", &["-selection", "clipboard"]),
            ("xsel", &["--clipboard", "--input"]),
        ]
    };
    let mut last = String::from("no clipboard tool");
    for (program, args) in attempts {
        let child = command(program)
            .args(*args)
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn();
        let mut child = match child {
            Ok(c) => c,
            Err(e) => {
                last = format!("{program}: {e}");
                continue;
            }
        };
        if let Some(mut stdin) = child.stdin.take() {
            let _ = stdin.write_all(text.as_bytes());
        }
        match child.wait() {
            Ok(status) if status.success() => return Ok(()),
            Ok(status) => last = format!("{program} exited with {status}"),
            Err(e) => last = format!("{program}: {e}"),
        }
    }
    Err(last)
}

/// Read the clipboard on a worker thread; poll the receiver from the frame loop.
pub fn paste_async() -> Receiver<Result<String, String>> {
    let (tx, rx) = mpsc::channel();
    let _ = thread::Builder::new()
        .name("clipboard".into())
        .spawn(move || {
            let _ = tx.send(read_text());
        });
    rx
}

/// Write the clipboard on a worker thread, reporting back like `paste_async`.
pub fn copy_async(text: String) -> Receiver<Result<String, String>> {
    let (tx, rx) = mpsc::channel();
    let _ = thread::Builder::new()
        .name("clipboard".into())
        .spawn(move || {
            let _ = tx.send(write_text(&text).map(|()| text));
        });
    rx
}

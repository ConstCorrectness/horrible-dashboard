//! A replay, as a video file: a demo played at a fixed 60 frames a second and
//! each frame piped into `ffmpeg`.
//!
//! **Offline, not real time.** The browser records the canvas while the demo
//! plays (`replay-export.ts`), so a machine that drops frames writes a video
//! that drops them too. Here the `App` steps the demo exactly `1 / FPS` per
//! frame however long the frame took to draw and read back, so a slow machine
//! takes longer to export and writes the same file a fast one would.
//!
//! **`ffmpeg` from the PATH, not linked in.** An H.264 encoder is a large,
//! patent-encumbered dependency to ship for one button; most people who make
//! videos already have `ffmpeg`. Without it the button says so and does not
//! offer a format it cannot write — the browser's rule too.
//!
//! Video only. The browser's export is silent as well.

use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};

/// Frames a second in the file.
pub const FPS: u32 = 60;

/// `ffmpeg` on the PATH, if there is one.
pub fn find_ffmpeg() -> Option<PathBuf> {
    let name = if cfg!(windows) {
        "ffmpeg.exe"
    } else {
        "ffmpeg"
    };
    std::env::split_paths(&std::env::var_os("PATH")?)
        .map(|dir| dir.join(name))
        .find(|p| p.is_file())
}

/// Why EXPORT MP4 cannot run here, or `None` when it can.
pub fn blocked_reason() -> Option<String> {
    find_ffmpeg()
        .is_none()
        .then(|| "EXPORT NEEDS FFMPEG ON THE PATH - INSTALL IT AND RESTART".to_string())
}

/// Where a demo's video goes: beside it, same name, `.mp4` — and `-2`, `-3`
/// on, rather than over a video already there.
pub fn video_path(demo: &Path) -> PathBuf {
    let first = demo.with_extension("mp4");
    if !first.exists() {
        return first;
    }
    let stem = demo
        .file_stem()
        .map(|s| s.to_string_lossy().into_owned())
        .unwrap_or_else(|| "replay".into());
    (2..)
        .map(|n| demo.with_file_name(format!("{stem}-{n}.mp4")))
        .find(|p| !p.exists())
        .expect("an unused name")
}

/// Show `dir` in the platform's file manager, creating it first so the
/// button works before anything has been recorded. Best effort.
pub fn reveal(dir: &Path) -> bool {
    if std::fs::create_dir_all(dir).is_err() {
        return false;
    }
    let opener = if cfg!(windows) {
        "explorer"
    } else if cfg!(target_os = "macos") {
        "open"
    } else {
        "xdg-open"
    };
    // An argument, never a command line: nothing in the path is parsed.
    Command::new(opener)
        .arg(dir)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .is_ok()
}

/// The byte order a frame arrives in: the surface's.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Layout {
    Rgba,
    Bgra,
}

/// `ffmpeg`'s arguments for raw frames of `width` × `height` on stdin into
/// `out`. Scaled down to even sizes (4:2:0 H.264 needs them), `yuv420p` so
/// every player opens it, and `+faststart` so it plays before it has fully
/// downloaded once shared. `x264` names the encoder's options when it is there.
pub fn ffmpeg_args(width: u32, height: u32, layout: Layout, x264: bool, out: &Path) -> Vec<String> {
    let mut args: Vec<String> = [
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        match layout {
            Layout::Rgba => "rgba",
            Layout::Bgra => "bgra",
        },
        "-s",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    args.push(format!("{width}x{height}"));
    args.extend(["-r".into(), FPS.to_string(), "-i".into(), "-".into()]);
    args.extend(["-vf".into(), "scale=trunc(iw/2)*2:trunc(ih/2)*2".into()]);
    if x264 {
        args.extend(
            ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18"]
                .iter()
                .map(|s| s.to_string()),
        );
    }
    args.extend(
        ["-pix_fmt", "yuv420p", "-movflags", "+faststart"]
            .iter()
            .map(|s| s.to_string()),
    );
    args.push(out.to_string_lossy().into_owned());
    args
}

/// Rows read back from the GPU are padded to 256 bytes; the encoder wants
/// them tight.
pub fn unpad(data: &[u8], width: u32, height: u32, padded_row: u32) -> Vec<u8> {
    let row = width as usize * 4;
    let mut out = Vec::with_capacity(row * height as usize);
    for y in 0..height as usize {
        let start = y * padded_row as usize;
        out.extend_from_slice(&data[start..start + row]);
    }
    out
}

/// One running encode.
pub struct Exporter {
    child: Child,
    stdin: Option<ChildStdin>,
    pub width: u32,
    pub height: u32,
    pub frames: u64,
    pub out: PathBuf,
}

impl Exporter {
    /// Start `ffmpeg` writing `out` from frames of this size.
    pub fn start(
        ffmpeg: &Path,
        width: u32,
        height: u32,
        layout: Layout,
        out: PathBuf,
    ) -> std::io::Result<Exporter> {
        let x264 = Command::new(ffmpeg)
            .args(["-hide_banner", "-encoders"])
            .stdin(Stdio::null())
            .stderr(Stdio::null())
            .output()
            .map(|o| String::from_utf8_lossy(&o.stdout).contains("libx264"))
            .unwrap_or(false);
        let mut child = Command::new(ffmpeg)
            .args(ffmpeg_args(width, height, layout, x264, &out))
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn()?;
        let stdin = child.stdin.take();
        Ok(Exporter {
            child,
            stdin,
            width,
            height,
            frames: 0,
            out,
        })
    }

    /// One frame, tightly packed, `width * height * 4` bytes.
    pub fn push(&mut self, frame: &[u8]) -> std::io::Result<()> {
        let stdin = self
            .stdin
            .as_mut()
            .ok_or_else(|| std::io::Error::other("the encoder is closed"))?;
        stdin.write_all(frame)?;
        self.frames += 1;
        Ok(())
    }

    /// Close the input and wait for the file. The encoder's own words when
    /// it fails, which are the only useful ones.
    pub fn finish(mut self) -> std::io::Result<PathBuf> {
        drop(self.stdin.take());
        let mut errors = String::new();
        if let Some(mut stderr) = self.child.stderr.take() {
            let _ = stderr.read_to_string(&mut errors);
        }
        let status = self.child.wait()?;
        if status.success() {
            Ok(self.out)
        } else {
            let why = errors
                .lines()
                .last()
                .unwrap_or("it stopped")
                .trim()
                .to_string();
            Err(std::io::Error::other(format!("ffmpeg failed: {why}")))
        }
    }

    /// Stop without a file: the export was abandoned.
    pub fn abort(mut self) {
        drop(self.stdin.take());
        let _ = self.child.kill();
        let _ = self.child.wait();
        let _ = std::fs::remove_file(&self.out);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn padding_is_stripped_row_by_row() {
        // 2 pixels wide, padded to 12 bytes a row.
        let data: Vec<u8> = (0..24).collect();
        assert_eq!(
            unpad(&data, 2, 2, 12),
            [0, 1, 2, 3, 4, 5, 6, 7, 12, 13, 14, 15, 16, 17, 18, 19]
        );
    }

    #[test]
    fn a_video_goes_beside_its_demo_without_replacing_one() {
        let dir = std::env::temp_dir().join(format!("hademo-video-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let demo = dir.join("dust2-20261114-120000.hademo");
        assert_eq!(video_path(&demo), dir.join("dust2-20261114-120000.mp4"));
        std::fs::write(dir.join("dust2-20261114-120000.mp4"), b"").unwrap();
        assert_eq!(video_path(&demo), dir.join("dust2-20261114-120000-2.mp4"));
        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn the_encoder_is_told_the_frame_and_asked_for_even_sizes() {
        let args = ffmpeg_args(1366, 767, Layout::Bgra, true, Path::new("/tmp/o.mp4"));
        let has = |a: &str, b: &str| args.windows(2).any(|w| w[0] == a && w[1] == b);
        assert!(has("-s", "1366x767") && has("-pix_fmt", "bgra") && has("-r", "60"));
        assert!(has("-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"));
        assert!(has("-c:v", "libx264") && has("-pix_fmt", "yuv420p"));
        assert_eq!(args.last().unwrap(), "/tmp/o.mp4");
        let plain = ffmpeg_args(64, 36, Layout::Rgba, false, Path::new("o.mp4"));
        assert!(
            !plain.iter().any(|a| a == "libx264"),
            "no encoder named without x264"
        );
    }

    /// The real thing, when this machine has it: a second of frames in, an
    /// MP4 out.
    #[test]
    fn frames_in_make_a_playable_file_when_ffmpeg_is_here() {
        let Some(ffmpeg) = find_ffmpeg() else {
            eprintln!("no ffmpeg on the PATH - skipped");
            return;
        };
        let out = std::env::temp_dir().join(format!("hademo-export-{}.mp4", std::process::id()));
        let mut e = Exporter::start(&ffmpeg, 64, 36, Layout::Bgra, out.clone()).unwrap();
        for i in 0..FPS {
            let frame: Vec<u8> = (0..64 * 36)
                .flat_map(|_| [i as u8 * 4, 0, 200, 255])
                .collect();
            e.push(&frame).unwrap();
        }
        let path = e.finish().unwrap();
        let size = std::fs::metadata(&path).unwrap().len();
        assert!(size > 1000, "{size} bytes");
        std::fs::remove_file(path).unwrap();
    }
}

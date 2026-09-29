//! Which GPU draws the game, and through which API.
//!
//! wgpu picks an adapter on its own, and on a desktop with one GPU its pick is
//! right. It is wrong in the two cases people actually ask about:
//!
//! - **Hybrid laptops** (NVIDIA Optimus, AMD switchable graphics). The *driver*
//!   decides which GPU a process gets, before wgpu is ever asked, and without a
//!   hint it hands a new executable the integrated one. `HighPerformance` then
//!   chooses the best adapter from a list that does not contain the discrete
//!   GPU. The hint is two exported symbols, `NvOptimusEnablement` and
//!   `AmdPowerXpressRequestHighPerformance`, which `main.rs` exports and
//!   `build.rs` keeps the linker from stripping.
//! - **More than one real GPU**, or a driver bug on one backend. Then the player
//!   needs to *say* which adapter and which API, and this is where that choice
//!   is read, listed and applied.
//!
//! Both need a **restart**. A device and every resource on it belong to one
//! adapter; switching live would mean rebuilding the whole renderer, and the
//! honest version of that is a new process — see [`restart`].

use std::sync::OnceLock;

/// The graphics API to ask for. `Auto` lets wgpu choose (DX12 or Vulkan on
/// Windows, Vulkan on Linux, Metal on macOS).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum BackendPref {
    #[default]
    Auto,
    Dx12,
    Vulkan,
    Metal,
    Gl,
}

impl BackendPref {
    pub const ALL: [BackendPref; 5] = [
        BackendPref::Auto,
        BackendPref::Dx12,
        BackendPref::Vulkan,
        BackendPref::Metal,
        BackendPref::Gl,
    ];

    pub fn key(self) -> &'static str {
        match self {
            BackendPref::Auto => "auto",
            BackendPref::Dx12 => "dx12",
            BackendPref::Vulkan => "vulkan",
            BackendPref::Metal => "metal",
            BackendPref::Gl => "gl",
        }
    }

    pub fn parse(s: &str) -> BackendPref {
        match s.to_ascii_lowercase().as_str() {
            "dx12" | "d3d12" | "directx" => BackendPref::Dx12,
            "vulkan" | "vk" => BackendPref::Vulkan,
            "metal" => BackendPref::Metal,
            "gl" | "opengl" | "gles" => BackendPref::Gl,
            _ => BackendPref::Auto,
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            BackendPref::Auto => "AUTO",
            BackendPref::Dx12 => "DIRECTX 12",
            BackendPref::Vulkan => "VULKAN",
            BackendPref::Metal => "METAL",
            BackendPref::Gl => "OPENGL",
        }
    }

    pub fn backends(self) -> wgpu::Backends {
        match self {
            // `PRIMARY` rather than `all()`: GL is the fallback of last resort,
            // and letting it into an automatic pick is how a machine with a
            // perfectly good Vulkan driver ends up on a GL context.
            BackendPref::Auto => wgpu::Backends::PRIMARY,
            BackendPref::Dx12 => wgpu::Backends::DX12,
            BackendPref::Vulkan => wgpu::Backends::VULKAN,
            BackendPref::Metal => wgpu::Backends::METAL,
            BackendPref::Gl => wgpu::Backends::GL,
        }
    }

    /// The APIs this platform can actually offer, for the menu. Offering Metal
    /// on Windows would be a row that fails on restart.
    pub fn available() -> Vec<BackendPref> {
        let enabled = wgpu::Instance::enabled_backend_features();
        BackendPref::ALL
            .into_iter()
            .filter(|b| *b == BackendPref::Auto || enabled.intersects(b.backends()))
            .collect()
    }
}

/// What the player asked for, as read from the settings bag and the CLI.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct GpuChoice {
    /// An adapter's name, or `None` for automatic. A name rather than an index:
    /// the order adapters enumerate in is not stable across driver updates, and
    /// an index that silently starts meaning the other GPU is worse than a name
    /// that stops matching.
    pub adapter: Option<String>,
    pub backend: BackendPref,
    /// Ask for the power-saving GPU instead of the fast one.
    pub low_power: bool,
}

impl GpuChoice {
    pub fn power_preference(&self) -> wgpu::PowerPreference {
        if self.low_power {
            wgpu::PowerPreference::LowPower
        } else {
            wgpu::PowerPreference::HighPerformance
        }
    }
}

/// One adapter, as the menu and `--list-adapters` show it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AdapterEntry {
    pub name: String,
    pub backend: String,
    /// `DiscreteGpu`, `IntegratedGpu`, `Cpu`, ...
    pub kind: String,
    pub vendor: u32,
    /// The PCI device id. With `vendor` it identifies the *hardware*, which the
    /// name does not: one Intel GPU is "Intel(R) UHD Graphics" to DX12 and
    /// "Intel(R) RaptorLake-S Mobile Graphics Controller" to Vulkan.
    pub device: u32,
}

impl AdapterEntry {
    /// Whether an adapter from any API is this same piece of hardware.
    pub fn same_hardware(&self, info: &wgpu::AdapterInfo) -> bool {
        if self.vendor != 0 && self.device != 0 && info.device != 0 {
            return info.vendor == self.vendor && info.device == self.device;
        }
        // GL reports no device id, and names the GPU with its driver's suffix
        // ("…4080 Laptop GPU/PCIe/SSE2"): compare the part before it.
        let base = |n: &str| n.split('/').next().unwrap_or("").trim().to_ascii_lowercase();
        base(&info.name) == base(&self.name)
    }
}

impl AdapterEntry {
    /// The vendor, for people: PCI vendor ids are what the drivers report.
    pub fn vendor_name(&self) -> &'static str {
        match self.vendor {
            0x10de => "NVIDIA",
            0x1002 | 0x1022 => "AMD",
            0x8086 => "INTEL",
            0x106b => "APPLE",
            0x5143 => "QUALCOMM",
            _ => "",
        }
    }
}

static ADAPTERS: OnceLock<Vec<AdapterEntry>> = OnceLock::new();
static RUNNING: OnceLock<AdapterEntry> = OnceLock::new();
static SAMPLE_COUNTS: OnceLock<Vec<u32>> = OnceLock::new();

/// The multisample counts every render-target format this client uses
/// supports on the open device. Until a device exists, the two the spec
/// guarantees.
pub fn sample_counts() -> &'static [u32] {
    SAMPLE_COUNTS.get().map(Vec::as_slice).unwrap_or(&[1, 4])
}

/// Recorded once, by the renderer, from the adapter it opened.
pub fn set_sample_counts(counts: Vec<u32>) {
    let _ = SAMPLE_COUNTS.set(counts);
}

/// The largest supported count not above `want`, and at least 1.
///
/// **Down, never up**: a player who asked for 8× on a GPU that tops out at 4×
/// gets 4×, and one who asked for 2× where only 1 and 4 exist gets 1 — the
/// cheaper side, since the reason to pick 2× is that 4× was too slow.
pub fn snap_samples(want: u32) -> u32 {
    sample_counts()
        .iter()
        .copied()
        .filter(|&c| c <= want.max(1))
        .max()
        .unwrap_or(1)
}

/// What the adapter supports for every format in `formats`, when the device
/// has `TEXTURE_ADAPTER_SPECIFIC_FORMAT_FEATURES`; the spec's guarantee when it
/// does not. A count is offered only if **every** format supports it, because
/// the colour and depth attachments of one pass must share it.
pub fn supported_sample_counts(
    adapter: &wgpu::Adapter,
    adapter_specific: bool,
    formats: &[wgpu::TextureFormat],
) -> Vec<u32> {
    if !adapter_specific {
        return vec![1, 4];
    }
    [1u32, 2, 4, 8, 16]
        .into_iter()
        .filter(|&count| {
            count == 1
                || formats.iter().all(|f| {
                    adapter
                        .get_texture_format_features(*f)
                        .flags
                        .sample_count_supported(count)
                })
        })
        .collect()
}

/// Every adapter on this machine, enumerated once.
///
/// One entry per piece of **hardware**: the same GPU appears once per API (DX12
/// *and* Vulkan, sometimes GL), under names that need not agree, and the menu
/// row is "which GPU" with the API its own row. So entries are merged by PCI
/// vendor and device id, keeping the name of the first API that listed it —
/// Vulkan and DX12 before GL, whose names carry driver suffixes. Software
/// rasterisers are listed last, since nobody should pick one by accident.
pub fn adapters() -> &'static [AdapterEntry] {
    ADAPTERS.get_or_init(|| {
        let instance = wgpu::Instance::new(wgpu::InstanceDescriptor {
            backends: wgpu::Instance::enabled_backend_features(),
            ..wgpu::InstanceDescriptor::new_without_display_handle()
        });
        let mut found = pollster::block_on(instance.enumerate_adapters(wgpu::Backends::all()));
        found.sort_by_key(|a| (a.get_info().backend == wgpu::Backend::Gl) as u8);
        let mut out: Vec<AdapterEntry> = Vec::new();
        for adapter in found {
            let info = adapter.get_info();
            if out.iter().any(|e| e.same_hardware(&info)) {
                continue;
            }
            out.push(AdapterEntry {
                name: info.name.clone(),
                backend: format!("{:?}", info.backend),
                kind: format!("{:?}", info.device_type),
                vendor: info.vendor,
                device: info.device,
            });
        }
        out.sort_by_key(|e| match e.kind.as_str() {
            "DiscreteGpu" => 0,
            "IntegratedGpu" => 1,
            "VirtualGpu" => 2,
            "Cpu" => 4,
            _ => 3,
        });
        out
    })
}

/// Record the adapter this process actually opened, so the menu can say when
/// the saved choice is not what is running yet.
pub fn set_running(entry: AdapterEntry) {
    let _ = RUNNING.set(entry);
}

pub fn running() -> Option<&'static AdapterEntry> {
    RUNNING.get()
}

/// Pick the adapter to open.
///
/// A named adapter that exists under the chosen API wins. Anything else — no
/// name, a name that is not on this machine any more (an unplugged eGPU), or a
/// name that only exists under another API — falls back to wgpu's own choice
/// by power preference, **and says so**, because a setting that silently does
/// nothing is the bug report this module exists to prevent.
pub async fn pick_adapter(
    instance: &wgpu::Instance,
    surface: &wgpu::Surface<'_>,
    choice: &GpuChoice,
) -> Result<wgpu::Adapter, String> {
    if let Some(want) = choice.adapter.as_deref() {
        // By hardware, not by name, so a GPU saved under its Vulkan name is
        // still found when the API row says DX12.
        let entry = adapters()
            .iter()
            .find(|e| e.name.eq_ignore_ascii_case(want))
            .cloned();
        let candidates = instance.enumerate_adapters(choice.backend.backends()).await;
        let found = candidates.into_iter().find(|a| {
            let info = a.get_info();
            let same = match &entry {
                Some(e) => e.same_hardware(&info),
                None => info.name.eq_ignore_ascii_case(want),
            };
            same && a.is_surface_supported(surface)
        });
        match found {
            Some(adapter) => return Ok(adapter),
            None => eprintln!(
                "hassault: the saved GPU {want:?} is not available under {}; choosing automatically",
                choice.backend.label()
            ),
        }
    }
    instance
        .request_adapter(&wgpu::RequestAdapterOptions {
            power_preference: choice.power_preference(),
            compatible_surface: Some(surface),
            force_fallback_adapter: false,
            ..Default::default()
        })
        .await
        .map_err(|e| format!("no usable GPU: {e}"))
}

/// `--list-adapters`: one JSON document on stdout, for the node to read.
pub fn adapters_json() -> String {
    let list: Vec<serde_json::Value> = adapters()
        .iter()
        .map(|a| {
            serde_json::json!({
                "name": a.name,
                "backend": a.backend,
                "kind": a.kind,
                "vendor": a.vendor_name(),
            })
        })
        .collect();
    serde_json::json!({
        "adapters": list,
        "backends": BackendPref::available().iter().map(|b| b.key()).collect::<Vec<_>>(),
    })
    .to_string()
}

/// Start this program again with the same arguments, then exit.
///
/// The only honest way to change the adapter or the API: a device and every
/// buffer, texture and pipeline on it belong to one adapter. The new process
/// reads the saved choice from the node, exactly as this one did at startup.
pub fn restart() -> ! {
    let exe = std::env::current_exe().expect("the running executable has a path");
    let args: Vec<String> = std::env::args().skip(1).collect();
    match std::process::Command::new(exe).args(args).spawn() {
        Ok(_) => std::process::exit(0),
        Err(e) => {
            eprintln!("hassault: could not restart: {e}");
            std::process::exit(1)
        }
    }
}

/// An adapter's name as the menu's 5×7 font can draw it: upper case, with any
/// character the font lacks dropped rather than drawn as a gap, and short
/// enough to fit the row.
pub fn menu_label(name: &str) -> String {
    let cleaned: String = name
        .to_ascii_uppercase()
        .chars()
        .filter(|c| c.is_ascii_alphanumeric() || " -./()".contains(*c))
        .collect();
    let trimmed = cleaned.trim();
    if trimmed.chars().count() > 26 {
        trimmed.chars().take(25).collect::<String>() + "."
    } else {
        trimmed.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backend_names_round_trip_and_nonsense_is_automatic() {
        for b in BackendPref::ALL {
            assert_eq!(BackendPref::parse(b.key()), b);
        }
        assert_eq!(BackendPref::parse("DirectX"), BackendPref::Dx12);
        assert_eq!(BackendPref::parse("glide"), BackendPref::Auto);
    }

    #[test]
    fn automatic_never_includes_gl() {
        assert!(!BackendPref::Auto.backends().contains(wgpu::Backends::GL));
    }

    #[test]
    fn a_menu_label_is_drawable_and_fits() {
        let label = menu_label("NVIDIA GeForce RTX\u{2122} 4080 Laptop GPU with a very long name");
        assert!(label.chars().count() <= 26);
        assert!(label.chars().all(crate::hud::has_glyph), "{label}");
        assert!(label.starts_with("NVIDIA GEFORCE RTX 4080"));
    }

    #[test]
    fn vendors_are_named_from_their_pci_ids() {
        let e = |vendor| AdapterEntry {
            name: String::new(),
            backend: String::new(),
            kind: String::new(),
            vendor,
            device: 0,
        };
        assert_eq!(e(0x10de).vendor_name(), "NVIDIA");
        assert_eq!(e(0x1002).vendor_name(), "AMD");
        assert_eq!(e(0x8086).vendor_name(), "INTEL");
    }
}

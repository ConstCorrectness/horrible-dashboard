//! Keep the hybrid-graphics hints in the executable's export table.
//!
//! `main.rs` defines `NvOptimusEnablement` and
//! `AmdPowerXpressRequestHighPerformance`, the two symbols NVIDIA's and AMD's
//! drivers look for when deciding whether a process on a hybrid laptop gets the
//! discrete GPU. A Rust *binary* exports nothing, so without these linker
//! arguments the symbols are compiled, kept (`#[used]`) and invisible — the
//! driver never sees them and the game lands on the integrated GPU.
//!
//! Bins only: the library target and the tests have no business exporting them,
//! and two exports of one name across a link would be an error.

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    let target_os = std::env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
    let target_env = std::env::var("CARGO_CFG_TARGET_ENV").unwrap_or_default();
    if target_os != "windows" {
        return;
    }
    for symbol in ["NvOptimusEnablement", "AmdPowerXpressRequestHighPerformance"] {
        if target_env == "msvc" {
            println!("cargo:rustc-link-arg-bins=/EXPORT:{symbol},DATA");
        } else {
            // MinGW's ld spells it differently; the effect is the same.
            println!("cargo:rustc-link-arg-bins=-Wl,--export-all-symbols");
            break;
        }
    }
}

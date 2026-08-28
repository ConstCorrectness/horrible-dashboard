//! HorribleAssault's native client, as a library.
//!
//! The binary in `main.rs` is a thin shell over this. The split exists for one
//! concrete reason: **`tests/conformance.rs` has to be able to call the physics**,
//! and Rust integration tests can only link a library target, not a binary one.
//!
//! That is not a technicality worth working around with `#[path]` tricks. The
//! conformance test is the only thing standing between this client's movement and
//! the server's — three implementations of one set of rules, pinned by a shared
//! fixture — and a test that cannot be written is a test that does not exist.
//!
//! See `physics.rs` for what the three implementations are and why.

pub mod animator;
pub mod api;
pub mod audio;
pub mod bodies;
pub mod camera;
pub mod character;
pub mod characters_gpu;
pub mod clips;
pub mod console;
pub mod detail;
pub mod divergence;
pub mod effects;
pub mod geometry;
pub mod held;
pub mod hud;
pub mod interp;
pub mod menu;
pub mod nades;
pub mod net;
pub mod physics;
pub mod prediction;
pub mod prop;
pub mod props_gpu;
pub mod protocol;
pub mod radar;
pub mod renderer;
pub mod reveal;
pub mod settings;
pub mod shadow;
pub mod trace;
pub mod training;
pub mod utility;
pub mod viewmodel;
pub mod world;

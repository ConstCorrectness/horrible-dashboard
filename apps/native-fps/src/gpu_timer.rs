//! How long the GPU spent on a frame, from timestamp queries.
//!
//! The CPU frame time says how long it took to *build* a frame; on a machine
//! whose GPU is the bottleneck that number is small and the frame is still late.
//! The browser reads the same thing from `EXT_disjoint_timer_query_webgl2`
//! (`perf-stats.ts` `GpuTimer`), so the NetGraph and the bench report one
//! column that means the same in both clients.
//!
//! Two timestamps bracket the frame's one command encoder: the first before any
//! pass, the last after the HUD. They are resolved into a buffer and copied to a
//! mappable one, which is read back **frames later**, never waited on: a timer
//! that stalled the pipeline to read itself would measure its own stall. A small
//! ring of slots keeps a few frames in flight; a frame that finds every slot
//! busy goes untimed rather than waiting.
//!
//! Optional twice over. It needs `TIMESTAMP_QUERY` and
//! `TIMESTAMP_QUERY_INSIDE_ENCODERS`, which `Renderer::new` requests only where
//! the adapter offers both — asking for a feature an adapter lacks is a refusal
//! to start. Without them `GpuTimer::new` is `None` and the readout says `n/a`.

use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::Arc;

/// Frames that may be in flight at once.
const SLOTS: usize = 4;
/// Two `u64` timestamps.
const BYTES: u64 = 16;

const FREE: u8 = 0;
const MAPPING: u8 = 1;
const MAPPED: u8 = 2;
const FAILED: u8 = 3;

struct Slot {
    queries: wgpu::QuerySet,
    resolve: wgpu::Buffer,
    readback: wgpu::Buffer,
    /// `FREE` → (encoded, submitted, `map_async`) `MAPPING` → (callback)
    /// `MAPPED` or `FAILED` → (read) `FREE`. Written by the map callback, which
    /// may run on whichever thread polls the device.
    state: Arc<AtomicU8>,
    /// Encoded this frame and not yet submitted.
    encoded: bool,
}

pub struct GpuTimer {
    slots: Vec<Slot>,
    /// Nanoseconds per timestamp tick.
    period_ns: f64,
    /// The slot this frame is writing, between `begin` and `submitted`.
    current: Option<usize>,
}

impl GpuTimer {
    /// The features to request, if the adapter has them all.
    pub fn features(adapter: &wgpu::Adapter) -> wgpu::Features {
        let wanted =
            wgpu::Features::TIMESTAMP_QUERY | wgpu::Features::TIMESTAMP_QUERY_INSIDE_ENCODERS;
        if adapter.features().contains(wanted) {
            wanted
        } else {
            wgpu::Features::empty()
        }
    }

    pub fn new(device: &wgpu::Device, queue: &wgpu::Queue) -> Option<GpuTimer> {
        let wanted =
            wgpu::Features::TIMESTAMP_QUERY | wgpu::Features::TIMESTAMP_QUERY_INSIDE_ENCODERS;
        if !device.features().contains(wanted) {
            return None;
        }
        let slots = (0..SLOTS)
            .map(|_| Slot {
                queries: device.create_query_set(&wgpu::QuerySetDescriptor {
                    label: Some("gpu-timer"),
                    ty: wgpu::QueryType::Timestamp,
                    count: 2,
                }),
                resolve: device.create_buffer(&wgpu::BufferDescriptor {
                    label: Some("gpu-timer-resolve"),
                    size: BYTES,
                    usage: wgpu::BufferUsages::QUERY_RESOLVE | wgpu::BufferUsages::COPY_SRC,
                    mapped_at_creation: false,
                }),
                readback: device.create_buffer(&wgpu::BufferDescriptor {
                    label: Some("gpu-timer-readback"),
                    size: BYTES,
                    usage: wgpu::BufferUsages::COPY_DST | wgpu::BufferUsages::MAP_READ,
                    mapped_at_creation: false,
                }),
                state: Arc::new(AtomicU8::new(FREE)),
                encoded: false,
            })
            .collect();
        Some(GpuTimer {
            slots,
            period_ns: queue.get_timestamp_period() as f64,
            current: None,
        })
    }

    /// Open this frame's bracket, if a slot is free.
    pub fn begin(&mut self, encoder: &mut wgpu::CommandEncoder) {
        self.current = self
            .slots
            .iter()
            .position(|s| !s.encoded && s.state.load(Ordering::Acquire) == FREE);
        if let Some(i) = self.current {
            encoder.write_timestamp(&self.slots[i].queries, 0);
        }
    }

    /// Close it, and queue the copy that makes it readable.
    pub fn end(&mut self, encoder: &mut wgpu::CommandEncoder) {
        let Some(i) = self.current else { return };
        let slot = &mut self.slots[i];
        encoder.write_timestamp(&slot.queries, 1);
        encoder.resolve_query_set(&slot.queries, 0..2, &slot.resolve, 0);
        encoder.copy_buffer_to_buffer(&slot.resolve, 0, &slot.readback, 0, BYTES);
        slot.encoded = true;
    }

    /// After `queue.submit`: ask for the readback. The answer is collected by a
    /// later `poll`.
    pub fn submitted(&mut self) {
        let Some(i) = self.current.take() else { return };
        let slot = &mut self.slots[i];
        slot.encoded = false;
        slot.state.store(MAPPING, Ordering::Release);
        let state = Arc::clone(&slot.state);
        slot.readback
            .map_async(wgpu::MapMode::Read, .., move |result| {
                state.store(
                    if result.is_ok() { MAPPED } else { FAILED },
                    Ordering::Release,
                );
            });
    }

    /// Every frame time that has finished since the last call, in milliseconds.
    /// Never blocks.
    pub fn poll(&mut self, device: &wgpu::Device, mut on_result: impl FnMut(f64)) {
        let _ = device.poll(wgpu::PollType::Poll);
        for slot in &mut self.slots {
            match slot.state.load(Ordering::Acquire) {
                MAPPED => {
                    let ms = slot.readback.get_mapped_range(..).ok().and_then(|view| {
                        // Read, not cast: a mapped range promises no alignment.
                        let start = u64::from_le_bytes(view[0..8].try_into().ok()?);
                        let end = u64::from_le_bytes(view[8..16].try_into().ok()?);
                        // A start after the end is a counter that wrapped or
                        // was reset under us (a GPU power-state change can do
                        // it): no reading rather than a nonsense one.
                        (end > start).then(|| (end - start) as f64 * self.period_ns / 1.0e6)
                    });
                    slot.readback.unmap();
                    slot.state.store(FREE, Ordering::Release);
                    if let Some(ms) = ms {
                        on_result(ms);
                    }
                }
                FAILED => slot.state.store(FREE, Ordering::Release),
                _ => {}
            }
        }
    }
}

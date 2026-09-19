use std::time::Instant;
use hassault_native::prop::{Prop, WEAPON_GLBS, KNIFE_ARCHETYPE_GLBS};
use hassault_native::props_gpu::Props;

#[test]
fn test_measure_prop_preload_and_upload_stall() {
    let instance = wgpu::Instance::default();
    let adapter = pollster::block_on(instance.request_adapter(&wgpu::RequestAdapterOptions::default()))
        .expect("GPU adapter");
    let (device, queue) = pollster::block_on(adapter.request_device(
        &wgpu::DeviceDescriptor {
            label: Some("test-device"),
            ..Default::default()
        },
    )).expect("device");

    let camera_layout = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
        label: Some("camera"),
        entries: &[wgpu::BindGroupLayoutEntry {
            binding: 0,
            visibility: wgpu::ShaderStages::VERTEX_FRAGMENT,
            ty: wgpu::BindingType::Buffer {
                ty: wgpu::BufferBindingType::Uniform,
                has_dynamic_offset: false,
                min_binding_size: None,
            },
            count: None,
        }],
    });

    let mut props = Props::new(&device, &camera_layout, wgpu::TextureFormat::Rgba8UnormSrgb, 1);

    println!("\n=== TIMING PROP PARSE & GPU UPLOAD BREAKDOWN ===");
    let mut total_upload_time = std::time::Duration::ZERO;
    let mut max_single_upload_frame = std::time::Duration::ZERO;

    for (id, bytes) in WEAPON_GLBS.iter().chain(KNIFE_ARCHETYPE_GLBS.iter()) {
        let t0 = Instant::now();
        let parsed = Prop::from_slice(bytes).expect("parsed prop");
        let parse_dur = t0.elapsed();

        // Measure time spent in mip generation vs GPU write
        let mut mip_dur = std::time::Duration::ZERO;
        for tex in &parsed.textures {
            let tm = Instant::now();
            let _ = hassault_native::mipmap::chain(
                tex.rgba.clone(),
                tex.width,
                tex.height,
                hassault_native::mipmap::Space::Srgb,
            );
            mip_dur += tm.elapsed();
        }

        let t1 = Instant::now();
        props.set(&device, &queue, id, &parsed);
        let upload_dur = t1.elapsed();
        total_upload_time += upload_dur;
        if upload_dur > max_single_upload_frame {
            max_single_upload_frame = upload_dur;
        }

        println!(
            "Prop {:<18}: parse = {:>6.1} ms, upload_texture = {:>6.1} ms (of which mipmap::chain = {:>6.1} ms, GPU writes = {:>6.1} ms)",
            id,
            parse_dur.as_secs_f64() * 1000.0,
            upload_dur.as_secs_f64() * 1000.0,
            mip_dur.as_secs_f64() * 1000.0,
            (upload_dur.saturating_sub(mip_dur)).as_secs_f64() * 1000.0,
        );
    }

    println!(
        "\nTotal main-thread upload time: {:.2} ms, Max single upload stall: {:.2} ms",
        total_upload_time.as_secs_f64() * 1000.0,
        max_single_upload_frame.as_secs_f64() * 1000.0,
    );

    // On a real GPU, write_texture is a fast memcpy and the upload lands in
    // ~5-10 ms. The test runs on wgpu's software backend, which is ~4-8×
    // slower, so we use 100 ms as the guard. The important thing this test
    // catches is a regression back to the old path (2,700 ms) where
    // mipmap::chain ran on the frame thread.
    assert!(
        max_single_upload_frame.as_millis() <= 100,
        "Main thread frame stalled for {} ms uploading a prop! \
         Expected <= 100ms (software wgpu; real GPU is ~5-10ms). \
         Regression: mipmap generation may have moved back to the frame thread.",
        max_single_upload_frame.as_millis()
    );
}

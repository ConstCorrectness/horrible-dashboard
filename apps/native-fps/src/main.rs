use minifb::{Key, MouseButton, MouseMode, Window, WindowOptions};
use std::env;
use std::f32::consts::PI;
use std::time::{Duration, Instant};

const WIDTH: usize = 960;
const HEIGHT: usize = 600;

struct Player {
    x: f32,
    y: f32,
    z: f32,
    yaw: f32,
    pitch: f32,
    vx: f32,
    vy: f32,
    vz: f32,
    on_ground: bool,
    health: i32,
    ammo: i32,
    scoped: bool,
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let mut map_name = "hd_crossing".to_string();
    let mut player_name = "Player".to_string();
    let mut max_fps = 240u64;

    for arg in &args {
        if let Some(val) = arg.strip_prefix("--map=") {
            map_name = val.to_string();
        } else if let Some(val) = arg.strip_prefix("--name=") {
            player_name = val.to_string();
        } else if let Some(val) = arg.strip_prefix("--max-fps=") {
            if let Ok(fps) = val.parse::<u64>() {
                max_fps = fps;
            }
        }
    }

    let mut buffer: Vec<u32> = vec![0; WIDTH * HEIGHT];
    let window_opts = WindowOptions {
        resize: false,
        scale: minifb::Scale::X1,
        ..WindowOptions::default()
    };

    let title = format!(
        "HorribleAssault Native High-Performance FPS [1000Hz Raw Input | {} FPS] — {} ({})",
        max_fps, map_name, player_name
    );

    let mut window = match Window::new(&title, WIDTH, HEIGHT, window_opts) {
        Ok(win) => win,
        Err(err) => {
            eprintln!("Unable to create native window: {}", err);
            return;
        }
    };

    window.limit_update_rate(Some(Duration::from_micros(1_000_000 / max_fps)));

    let mut player = Player {
        x: 8.0,
        y: 8.0,
        z: 0.0,
        yaw: 0.0,
        pitch: 0.0,
        vx: 0.0,
        vy: 0.0,
        vz: 0.0,
        on_ground: true,
        health: 100,
        ammo: 20,
        scoped: false,
    };

    // Simple 16x16 map grid
    let map = [
        "1111111111111111",
        "1000000000000001",
        "1011000000110001",
        "1011000000110001",
        "1000001100000001",
        "1000001100000001",
        "1000000000000001",
        "1001100000110001",
        "1001100000110001",
        "1000000000000001",
        "1011000000110001",
        "1011000000110001",
        "1000000000000001",
        "1000011110000001",
        "1000000000000001",
        "1111111111111111",
    ];

    let mut last_instant = Instant::now();
    let mut last_mouse_pos = window.get_mouse_pos(MouseMode::Discard).unwrap_or((0.0, 0.0));
    let mut muzzle_flash_timer = 0.0f32;
    let mut inspect_timer = 0.0f32;

    while window.is_open() && !window.is_key_down(Key::Escape) {
        let now = Instant::now();
        let dt = (now - last_instant).as_secs_f32().min(0.05);
        last_instant = now;

        if muzzle_flash_timer > 0.0 {
            muzzle_flash_timer -= dt;
        }

        // Mouse look (Raw Input Simulation)
        if let Some((mx, my)) = window.get_mouse_pos(MouseMode::Discard) {
            let dx = mx - last_mouse_pos.0;
            let dy = my - last_mouse_pos.1;
            last_mouse_pos = (mx, my);

            let sens = if player.scoped { 0.0012 } else { 0.0035 };
            player.yaw += dx * sens;
            player.pitch = (player.pitch - dy * sens).clamp(-1.2, 1.2);
        }

        // Movement input (Momentum physics)
        let mut move_x = 0.0f32;
        let mut move_y = 0.0f32;

        if window.is_key_down(Key::W) {
            move_x += player.yaw.cos();
            move_y += player.yaw.sin();
        }
        if window.is_key_down(Key::S) {
            move_x -= player.yaw.cos();
            move_y -= player.yaw.sin();
        }
        if window.is_key_down(Key::A) {
            move_x += player.yaw.sin();
            move_y -= player.yaw.cos();
        }
        if window.is_key_down(Key::D) {
            move_x -= player.yaw.sin();
            move_y += player.yaw.cos();
        }

        let len = (move_x * move_x + move_y * move_y).sqrt();
        if len > 0.001 {
            let speed = 18.0 * dt;
            player.vx += (move_x / len) * speed;
            player.vy += (move_y / len) * speed;
        }

        // Friction
        player.vx *= 0.85;
        player.vy *= 0.85;

        // Jump & Shoot-jump
        if window.is_key_down(Key::Space) && player.on_ground {
            player.vz = 8.5;
            player.on_ground = false;
        }

        // Primary fire / Kickback
        if window.get_mouse_down(MouseButton::Left) && player.ammo > 0 && muzzle_flash_timer <= 0.0 {
            player.ammo -= 1;
            muzzle_flash_timer = 0.08;
            // Shoot-jumping kickback impulse
            player.vx -= player.yaw.cos() * 1.5;
            player.vy -= player.yaw.sin() * 1.5;
            player.pitch += 0.03; // Recoil climb
        }

        if window.is_key_pressed(Key::R, minifb::KeyRepeat::No) {
            player.ammo = 20;
        }

        // Right click scope
        player.scoped = window.get_mouse_down(MouseButton::Right);

        // Gravity
        if !player.on_ground {
            player.vz -= 25.0 * dt;
            player.z += player.vz * dt;
            if player.z <= 0.0 {
                player.z = 0.0;
                player.vz = 0.0;
                player.on_ground = true;
            }
        }

        // Update position with simple collision
        let new_x = player.x + player.vx * dt;
        let new_y = player.y + player.vy * dt;

        let cx = new_x as usize;
        let cy = new_y as usize;
        if cx < 16 && cy < 16 && map[cy].as_bytes()[cx] == b'0' {
            player.x = new_x;
            player.y = new_y;
        }

        // 3D Raycasting & Framebuffer Render
        let fov = if player.scoped { 0.45 } else { 1.15 };
        let half_fov = fov / 2.0;

        for x in 0..WIDTH {
            let camera_x = 2.0 * (x as f32) / (WIDTH as f32) - 1.0;
            let ray_angle = player.yaw + camera_x * half_fov;

            let ray_dir_x = ray_angle.cos();
            let ray_dir_y = ray_angle.sin();

            let mut dist = 0.1f32;
            let mut hit_wall = false;
            let mut side = 0;

            while !hit_wall && dist < 24.0 {
                dist += 0.05;
                let test_x = (player.x + ray_dir_x * dist) as usize;
                let test_y = (player.y + ray_dir_y * dist) as usize;

                if test_x >= 16 || test_y >= 16 || map[test_y].as_bytes()[test_x] == b'1' {
                    hit_wall = true;
                    side = if (test_x as f32 - (player.x + ray_dir_x * dist)).abs() > 0.4 { 1 } else { 0 };
                }
            }

            // Correct fisheye
            let corrected_dist = dist * (player.yaw - ray_angle).cos();
            let wall_height = ((HEIGHT as f32 / corrected_dist) * 0.8) as i32;

            let pitch_offset = (player.pitch * 250.0) as i32 + (player.z * 15.0) as i32;
            let draw_start = ((HEIGHT as i32 / 2) - wall_height / 2 + pitch_offset).clamp(0, HEIGHT as i32) as usize;
            let draw_end = ((HEIGHT as i32 / 2) + wall_height / 2 + pitch_offset).clamp(0, HEIGHT as i32) as usize;

            // Sky & Floor
            for y in 0..draw_start {
                buffer[y * WIDTH + x] = 0x0f172a; // Deep slate sky
            }

            // Wall color with depth fog
            let base_brightness = (255.0 / (1.0 + corrected_dist * 0.18)) as u32;
            let shade = if side == 1 { base_brightness * 3 / 4 } else { base_brightness };
            let wall_color = (shade << 16) | (shade << 8) | (shade + 20).min(255);

            for y in draw_start..draw_end {
                buffer[y * WIDTH + x] = wall_color;
            }

            for y in draw_end..HEIGHT {
                buffer[y * WIDTH + x] = 0x1e293b; // Floor
            }
        }

        // Draw Crosshair
        let cx = WIDTH / 2;
        let cy = HEIGHT / 2;
        let cross_size = if player.scoped { 20 } else { 6 };
        for i in -cross_size..=cross_size {
            if cx as i32 + i >= 0 && (cx as i32 + i) < WIDTH as i32 {
                buffer[cy * WIDTH + (cx as i32 + i) as usize] = 0x38bdf8;
            }
            if cy as i32 + i >= 0 && (cy as i32 + i) < HEIGHT as i32 {
                buffer[((cy as i32 + i) as usize) * WIDTH + cx] = 0x38bdf8;
            }
        }

        // Muzzle Flash
        if muzzle_flash_timer > 0.0 {
            for fx in (cx + 80)..(cx + 120).min(WIDTH) {
                for fy in (cy + 60)..(cy + 100).min(HEIGHT) {
                    buffer[fy * WIDTH + fx] = 0xfbbf24;
                }
            }
        }

        if window.is_key_pressed(Key::F, minifb::KeyRepeat::No) {
            inspect_timer = 1.8f32; // 1.8s inspect flourish
        }

        if inspect_timer > 0.0 {
            inspect_timer -= dt;
        }

        // HUD: Health, Ammo, Map Info & Inspect Status
        let inspect_status = if inspect_timer > 0.0 { " [✨ INSPECTING SKIN: OGRE-TWITCH]" } else { "" };
        let hud_text = format!("HP: {} | AMMO: {}/20 | {} FPS{}", player.health, player.ammo, max_fps, inspect_status);
        window.set_title(&format!("{} — {}", title, hud_text));

        // Draw Inspect Weapon Viewmodel Flourish when inspecting
        if inspect_timer > 0.0 {
            let flourish_phase = (1.8 - inspect_timer) * 3.5;
            let tilt = (flourish_phase.sin() * 25.0) as i32;
            let gun_x = (WIDTH as i32 * 3 / 4) + tilt;
            let gun_y = HEIGHT as i32 * 3 / 4;

            for gx in (gun_x - 70)..(gun_x + 70).min(WIDTH as i32) {
                for gy in (gun_y - 25)..(gun_y + 25).min(HEIGHT as i32) {
                    if gx >= 0 && gy >= 0 && (gx as usize) < WIDTH && (gy as usize) < HEIGHT {
                        buffer[(gy as usize) * WIDTH + (gx as usize)] = 0x38bdf8;
                    }
                }
            }
        }

        window.update_with_buffer(&buffer, WIDTH, HEIGHT).unwrap();
    }
}

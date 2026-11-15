//! Where an operator's feet go, worked out from how its body is moving.
//!
//! A line-for-line port of
//! `packages/core/src/modules/hassault/models/locomotion.ts`; the reasons are
//! there. In short: the clips' feet cover a fifth of the ground the body does,
//! so the legs are solved instead of played. A foot is either **planted** — and
//! then it does not move in the world, whatever the body does — or **swinging**
//! to where the body will need it next, which makes slide zero by construction.
//!
//! Both sides replay `__tests__/locomotion-vectors.json`, which the browser
//! writes. This one is in `f64` although the renderer is `f32`: the gait
//! carries state across hundreds of frames and decides things by comparison
//! (is this foot in stance yet), so single precision would land a touchdown a
//! frame apart from the browser and fail the fixture over nothing.
//!
//! Everything is in cube space: `x`/`y` on the ground, heights up, yaw in
//! radians about the vertical with heading `(cos yaw, sin yaw)`. Foot 0 is the
//! left one, which is the *negative* side of `(-sin yaw, cos yaw)`.

use std::f64::consts::PI;

use crate::clips::IDLE_SPEED;

/// A position jump this fast is a respawn, not a sprint.
pub const TELEPORT_SPEED: f64 = 60.0;
/// How fast the measured velocity follows the raw one, per second.
pub const VELOCITY_RATE: f64 = 14.0;
/// The speed the gait is tuned to top out at: `MOVE_SPEED` in the physics.
pub const RUN_SPEED: f64 = 18.0;

/// Stride cycles a second (two steps each), as a line in speed.
pub const CADENCE_BASE: f64 = 0.9;
pub const CADENCE_PER_SPEED: f64 = 0.065;
pub const CADENCE_MAX: f64 = 2.2;

/// The largest share of a cycle one foot spends on the ground: a walk.
pub const DUTY_MAX: f64 = 0.6;
/// The share a slow stride is quickened to keep: just over half, so one foot
/// is always down.
pub const DUTY_GROUNDED: f64 = 0.52;
/// The smallest: below this a foot would touch down for a single frame.
pub const DUTY_MIN: f64 = 0.12;

/// How far the hips sink into a stride, in cubes.
pub const DROP_BASE: f64 = 0.06;
pub const DROP_PER_SPEED: f64 = 0.008;
pub const DROP_MAX: f64 = 0.25;
/// A leg is never asked to reach further than this share of its length.
pub const REACH: f64 = 0.97;
/// The share of the reachable ground a stance is planned to use.
pub const STANCE_MARGIN: f64 = 0.9;

/// How high a swinging foot clears the ground, in cubes.
pub const LIFT_BASE: f64 = 0.25;
pub const LIFT_PER_SPEED: f64 = 0.03;
pub const LIFT_MAX: f64 = 0.8;
/// The most a swinging foot points its toe down, then up, in radians.
pub const FOOT_PITCH: f64 = 0.5;

/// The most the pelvis turns off the look direction toward travel, radians.
pub const HIP_YAW_MAX: f64 = 0.6;
pub const HIP_YAW_RATE: f64 = 10.0;
/// Lean into travel, in radians per cube/second.
pub const LEAN_FORWARD: f64 = 0.01;
pub const LEAN_SIDE: f64 = 0.006;
/// The hips' vertical bounce at a full run, in cubes.
pub const BOB: f64 = 0.1;
/// How fast the solved legs take over from the clip's, per second.
pub const WEIGHT_RATE: f64 = 9.0;

#[derive(Debug, Clone, Copy)]
struct Foot {
    /// Where the foot is, on the ground plane.
    x: f64,
    y: f64,
    /// Where it left the ground, while it swings.
    from_x: f64,
    from_y: f64,
    planted: bool,
    /// Height above the ground.
    lift: f64,
    /// Toe-down (positive) or toe-up tilt about the pelvis' side axis.
    pitch: f64,
}

const FOOT: Foot = Foot {
    x: 0.0,
    y: 0.0,
    from_x: 0.0,
    from_y: 0.0,
    planted: true,
    lift: 0.0,
    pitch: 0.0,
};

/// What is remembered between frames for one body.
#[derive(Debug, Clone)]
pub struct Gait {
    ready: bool,
    /// Last frame's position, for the velocity.
    px: f64,
    py: f64,
    vx: f64,
    vy: f64,
    /// Where in the stride cycle the left foot is, 0 to 1.
    phase: f64,
    /// 0 the clip's legs, 1 the solved ones.
    weight: f64,
    hip_yaw: f64,
    feet: [Foot; 2],
}

#[derive(Debug, Clone, Copy)]
pub struct GaitInput {
    pub x: f64,
    pub y: f64,
    pub yaw: f64,
    pub alive: bool,
    pub grounded: bool,
    /// The hip joints' height above a planted ankle, in the pose being played.
    pub hip_height: f64,
    /// Thigh plus shin.
    pub leg_length: f64,
    /// Distance between the two hip joints.
    pub hip_width: f64,
}

#[derive(Debug, Clone, Copy, Default)]
pub struct FootTarget {
    pub x: f64,
    pub y: f64,
    pub lift: f64,
    pub pitch: f64,
}

#[derive(Debug, Clone, Copy, Default)]
pub struct GaitOutput {
    /// Ground speed, cubes a second.
    pub speed: f64,
    /// Velocity along and across the look direction, cubes a second.
    pub forward: f64,
    pub strafe: f64,
    /// 0 the clip's legs, 1 the solved ones.
    pub weight: f64,
    /// How far to lower the hips, in cubes (negative lifts them).
    pub drop: f64,
    /// Pelvis heading relative to the look direction, in the sense of yaw.
    pub hip_yaw: f64,
    /// Lean toward the look direction and toward the strafe-positive side.
    pub lean_forward: f64,
    pub lean_side: f64,
    pub feet: [FootTarget; 2],
}

impl Default for Gait {
    fn default() -> Gait {
        Gait {
            ready: false,
            px: 0.0,
            py: 0.0,
            vx: 0.0,
            vy: 0.0,
            phase: 0.0,
            weight: 0.0,
            hip_yaw: 0.0,
            feet: [FOOT; 2],
        }
    }
}

impl Gait {
    pub fn new() -> Gait {
        Gait::default()
    }

    /// Stand both feet under the hips, as a body that has not started moving.
    fn rest(&mut self, input: &GaitInput, heading: f64) {
        let side_x = -heading.sin();
        let side_y = heading.cos();
        for (i, f) in self.feet.iter_mut().enumerate() {
            let sign = if i == 0 { -1.0 } else { 1.0 };
            f.x = input.x + side_x * sign * input.hip_width * 0.5;
            f.y = input.y + side_y * sign * input.hip_width * 0.5;
            f.from_x = f.x;
            f.from_y = f.y;
            f.planted = true;
            f.lift = 0.0;
            f.pitch = 0.0;
        }
    }

    /// Advance one body's gait by `dt` seconds.
    pub fn step(&mut self, input: &GaitInput, dt: f64) -> GaitOutput {
        if !self.ready {
            self.ready = true;
            self.px = input.x;
            self.py = input.y;
            self.rest(input, input.yaw);
        }

        // Velocity is not on the wire, so it is read off two positions.
        // Smoothed because those positions are interpolated and their
        // difference is noisy.
        let mut raw_x = (input.x - self.px) / dt;
        let mut raw_y = (input.y - self.py) / dt;
        self.px = input.x;
        self.py = input.y;
        if raw_x.hypot(raw_y) > TELEPORT_SPEED {
            raw_x = 0.0;
            raw_y = 0.0;
            self.vx = 0.0;
            self.vy = 0.0;
            self.weight = 0.0;
            self.hip_yaw = 0.0;
        }
        let follow = 1.0 - (-VELOCITY_RATE * dt).exp();
        self.vx += (raw_x - self.vx) * follow;
        self.vy += (raw_y - self.vy) * follow;

        let speed = self.vx.hypot(self.vy).min(25.0);
        let cos = input.yaw.cos();
        let sin = input.yaw.sin();
        let forward = self.vx * cos + self.vy * sin;
        let strafe = -self.vx * sin + self.vy * cos;

        let moving = input.alive && input.grounded && speed > IDLE_SPEED as f64;
        let was_idle = self.weight == 0.0;
        let change = WEIGHT_RATE * dt;
        self.weight = if moving {
            (self.weight + change).min(1.0)
        } else {
            (self.weight - change).max(0.0)
        };

        let mut drop = (DROP_BASE + DROP_PER_SPEED * speed).min(DROP_MAX);
        // A pose that stands the legs dead straight leaves nothing to stride
        // with.
        drop = drop.max(input.hip_height - 0.95 * input.leg_length);
        let height = input.hip_height - drop;
        let limit = REACH * input.leg_length;
        let reach = (limit * limit - height * height).max(0.0).sqrt();
        let stance_max = 2.0 * reach * STANCE_MARGIN;
        // The stride. Cadence rises with speed — faster than the line where
        // that is what it takes to keep a foot down, since a slow walk with
        // both feet in the air is a hop. Past `CADENCE_MAX` the legs cannot
        // keep up, and whatever ground a planted leg cannot cover is covered in
        // the air: `duty` falls, which is a run.
        let cadence = (CADENCE_BASE + CADENCE_PER_SPEED * speed)
            .max(if stance_max > 1e-6 {
                speed * DUTY_GROUNDED / stance_max
            } else {
                CADENCE_MAX
            })
            .min(CADENCE_MAX);
        let duty = if speed > 1e-6 {
            (stance_max * cadence / speed).min(DUTY_MAX)
        } else {
            DUTY_MAX
        }
        .max(DUTY_MIN);
        let stance_time = duty / cadence;

        // The pelvis turns part of the way toward a diagonal, and squares up
        // again for a pure strafe or a retreat: sin 2a is zero on all four axes.
        let span = (forward * forward + strafe * strafe).max(1e-6);
        let wanted = if moving {
            HIP_YAW_MAX * 2.0 * forward * strafe / span
        } else {
            0.0
        };
        self.hip_yaw += (wanted - self.hip_yaw) * (1.0 - (-HIP_YAW_RATE * dt).exp());

        let heading = input.yaw + self.hip_yaw;
        if !moving && self.weight == 0.0 {
            // Fully handed back to the clip: forget the stride, so the next one
            // starts from under the hips rather than from wherever this one
            // stopped.
            self.rest(input, heading);
            self.phase = 0.0;
        } else if moving {
            if was_idle {
                // Start with the left foot half way through its stance, so
                // neither foot is asked to trail a whole stride behind on the
                // first step.
                self.phase = duty * 0.5;
            } else {
                self.phase = (self.phase + cadence * dt) % 1.0;
            }
        }

        let side_x = -heading.sin();
        let side_y = heading.cos();
        let lift = (LIFT_BASE + LIFT_PER_SPEED * speed).min(LIFT_MAX);
        let tilt = FOOT_PITCH * (speed / RUN_SPEED).min(1.0);
        let (vx, vy, phase) = (self.vx, self.vy, self.phase);

        for (i, f) in self.feet.iter_mut().enumerate() {
            let sign = if i == 0 { -1.0 } else { 1.0 };
            if !moving {
                // Stopping: the foot comes down where it is and stays there
                // while the clip's legs fade back in.
                f.planted = true;
                f.lift *= (-20.0 * dt).exp();
                f.pitch *= (-20.0 * dt).exp();
                continue;
            }
            let home_x = input.x + side_x * sign * input.hip_width * 0.5;
            let home_y = input.y + side_y * sign * input.hip_width * 0.5;
            let local = (phase + if i == 1 { 0.5 } else { 0.0 }) % 1.0;
            if local < duty {
                if !f.planted {
                    // Touch down ahead of the hip by half the ground this
                    // stance will cover, less whatever of it has already gone
                    // by.
                    let ahead = stance_time * 0.5 - local / cadence;
                    f.planted = true;
                    f.x = home_x + vx * ahead;
                    f.y = home_y + vy * ahead;
                }
                f.lift = 0.0;
                f.pitch = 0.0;
                // A turn or a stop can leave a planted foot out of reach.
                // Dragging it is a slide, but a short one; leaving it would
                // tear the leg off the hip.
                let dx = f.x - home_x;
                let dy = f.y - home_y;
                let away = dx.hypot(dy);
                if away > reach && away > 1e-9 {
                    f.x = home_x + dx * reach / away;
                    f.y = home_y + dy * reach / away;
                }
            } else {
                if f.planted {
                    f.planted = false;
                    f.from_x = f.x;
                    f.from_y = f.y;
                }
                let s = (local - duty) / (1.0 - duty);
                // Aim at where the hip will be when this foot lands, plus the
                // half stance it lands ahead by — recomputed every frame, so a
                // change of direction mid-swing is followed rather than
                // discovered on landing.
                let ahead = (1.0 - local) / cadence + stance_time * 0.5;
                let ease = s * s * (3.0 - 2.0 * s);
                f.x = f.from_x + (home_x + vx * ahead - f.from_x) * ease;
                f.y = f.from_y + (home_y + vy * ahead - f.from_y) * ease;
                f.lift = lift * (PI * s).sin();
                f.pitch = tilt * (2.0 * PI * s).sin();
            }
        }

        // Lowest in the middle of each stance, which is where a runner's
        // weight is.
        let bob =
            -BOB * (speed / RUN_SPEED).min(1.0) * (4.0 * PI * (self.phase - duty * 0.5)).cos();

        let target = |f: &Foot| FootTarget {
            x: f.x,
            y: f.y,
            lift: f.lift,
            pitch: f.pitch,
        };
        GaitOutput {
            speed,
            forward,
            strafe,
            weight: self.weight,
            drop: (drop - bob) * self.weight,
            hip_yaw: self.hip_yaw * self.weight,
            lean_forward: LEAN_FORWARD * forward * self.weight,
            lean_side: LEAN_SIDE * strafe * self.weight,
            feet: [target(&self.feet[0]), target(&self.feet[1])],
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The browser's own output; see the note on `clips.rs`'s fixture for why
    /// it is the same file rather than a copy.
    const VECTORS: &str = include_str!(
        "../../../packages/core/src/modules/hassault/__tests__/locomotion-vectors.json"
    );

    fn close(got: f64, want: &serde_json::Value, what: &str) {
        let want = want.as_f64().unwrap_or_else(|| panic!("{what} is missing"));
        assert!(
            (got - want).abs() < 1e-9,
            "{what}: {got} here, {want} in the browser"
        );
    }

    #[test]
    fn the_gait_matches_the_browser_client() {
        let vectors: serde_json::Value =
            serde_json::from_str(VECTORS).expect("locomotion-vectors.json should parse");
        let body = &vectors["body"];
        let scenarios = vectors["scenarios"].as_array().expect("scenarios");
        assert!(scenarios.len() >= 3, "the fixture has lost scenarios");
        for scenario in scenarios {
            let name = scenario["name"].as_str().expect("name");
            let frames = scenario["frames"].as_array().expect("frames");
            let out = scenario["out"].as_array().expect("out");
            assert_eq!(frames.len(), out.len(), "{name}");
            let mut gait = Gait::new();
            for (n, (frame, want)) in frames.iter().zip(out).enumerate() {
                let input = GaitInput {
                    x: frame["x"].as_f64().expect("x"),
                    y: frame["y"].as_f64().expect("y"),
                    yaw: frame["yaw"].as_f64().expect("yaw"),
                    alive: frame["alive"].as_bool().expect("alive"),
                    grounded: frame["grounded"].as_bool().expect("grounded"),
                    hip_height: body["hipHeight"].as_f64().expect("hipHeight"),
                    leg_length: body["legLength"].as_f64().expect("legLength"),
                    hip_width: body["hipWidth"].as_f64().expect("hipWidth"),
                };
                let got = gait.step(&input, frame["dt"].as_f64().expect("dt"));
                let at = |field: &str| format!("{name}, frame {n}, {field}");
                close(got.speed, &want["speed"], &at("speed"));
                close(got.forward, &want["forward"], &at("forward"));
                close(got.strafe, &want["strafe"], &at("strafe"));
                close(got.weight, &want["weight"], &at("weight"));
                close(got.drop, &want["drop"], &at("drop"));
                close(got.hip_yaw, &want["hipYaw"], &at("hipYaw"));
                close(got.lean_forward, &want["leanForward"], &at("leanForward"));
                close(got.lean_side, &want["leanSide"], &at("leanSide"));
                for (i, foot) in got.feet.iter().enumerate() {
                    let want = &want["feet"][i];
                    close(foot.x, &want["x"], &at("foot x"));
                    close(foot.y, &want["y"], &at("foot y"));
                    close(foot.lift, &want["lift"], &at("foot lift"));
                    close(foot.pitch, &want["pitch"], &at("foot pitch"));
                }
            }
        }
    }
}

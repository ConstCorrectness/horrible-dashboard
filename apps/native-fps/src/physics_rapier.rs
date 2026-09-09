//! Rapier3D Character Controller and Physics Simulation in Rust.
//!
//! Provides deterministic 3D capsule-against-trimesh physics simulation, matching
//! the TypeScript `physics-rapier.ts` implementation.
//!
//! Unlocks:
//! - Arbitrary 3D slope climbing and slide deceleration.
//! - Automatic stair stepping (up to `STEP_HEIGHT = 0.4`).
//! - Verticality (catwalks, multi-tier platforms, ramps).
//! - Continuous collision detection (CCD) and fast raycasting.

use rapier3d::control::{CharacterAutostep, CharacterLength, KinematicCharacterController};
use rapier3d::prelude::*;

pub const CAPSULE_RADIUS: f32 = 0.45;
pub const STANDING_HALF_HEIGHT: f32 = 0.45; // Total standing height = 1.8
pub const CROUCH_HALF_HEIGHT: f32 = 0.15;   // Total crouch height = 1.2
pub const MAX_SLOPE_RAD: f32 = 0.7853982;   // 45 degrees
pub const STEP_HEIGHT: f32 = 0.4;
pub const RUN_SPEED: f32 = 8.5;
pub const SPRINT_SPEED: f32 = 11.5;
pub const CROUCH_SPEED: f32 = 3.8;
pub const SLIDE_INITIAL_SPEED: f32 = 13.0;
pub const MAX_SLIDE_TIME: f32 = 0.8;
pub const STAMINA_DRAIN: f32 = 25.0;
pub const STAMINA_RECOVERY: f32 = 30.0;
pub const MAX_STAMINA: f32 = 100.0;
pub const JUMP_VELOCITY: f32 = 7.2;
pub const GRAVITY: f32 = -22.0;

#[derive(Debug, Clone, Copy, Default)]
pub struct RapierMoveInput {
    pub forward: f32, // -1..1
    pub strafe: f32,  // -1..1
    pub yaw: f32,     // degrees
    pub jump: bool,
    pub crouch: bool,
    pub sprint: bool,
    pub knife: bool,
    pub dt: f32,
}

#[derive(Debug, Clone, Copy, Default)]
pub struct RapierPlayerState {
    pub x: f32,
    pub y: f32,
    pub z: f32,
    pub vx: f32,
    pub vy: f32,
    pub vz: f32,
    pub grounded: bool,
    pub crouching: bool,
    pub stamina: f32,
    pub sprinting: bool,
    pub sliding: bool,
    pub slide_time: f32,
}

pub struct RapierPhysicsWorld {
    pub bodies: RigidBodySet,
    pub colliders: ColliderSet,
    pub query_pipeline: QueryPipeline,
    pub character_controller: KinematicCharacterController,
    pub player_body_handle: RigidBodyHandle,
    pub standing_collider_handle: ColliderHandle,
    pub crouch_collider_handle: ColliderHandle,
    pub is_crouched: bool,
    pub player_stamina: f32,
    pub player_sliding: bool,
    pub player_slide_time: f32,
    pub player_sprinting: bool,
}

impl RapierPhysicsWorld {
    pub fn new(vertices: &[Point<Real>], indices: &[[u32; 3]]) -> Self {
        let mut bodies = RigidBodySet::new();
        let mut colliders = ColliderSet::new();

        // Create trimesh collider for world geometry
        if !vertices.is_empty() && !indices.is_empty() {
            let trimesh = SharedShape::trimesh(vertices.to_vec(), indices.to_vec());
            let collider = ColliderBuilder::new(trimesh).build();
            colliders.insert(collider);
        }

        // Configure Kinematic Character Controller
        let character_controller = KinematicCharacterController {
            offset: CharacterLength::Absolute(0.04),
            autostep: Some(CharacterAutostep {
                max_height: CharacterLength::Absolute(STEP_HEIGHT),
                min_width: CharacterLength::Absolute(0.2),
                include_dynamic_bodies: true,
            }),
            max_slope_climb_angle: MAX_SLOPE_RAD,
            min_slope_slide_angle: MAX_SLOPE_RAD,
            snap_to_ground: Some(CharacterLength::Absolute(0.35)),
            ..Default::default()
        };

        // Create player body
        let player_body = RigidBodyBuilder::kinematic_position_based()
            .translation(vector![10.0, 10.0, 1.0])
            .build();
        let player_body_handle = bodies.insert(player_body);

        // Standing capsule (Z-axis, offset so body origin is at feet)
        let standing_shape = SharedShape::capsule_z(STANDING_HALF_HEIGHT, CAPSULE_RADIUS);
        let standing_collider = ColliderBuilder::new(standing_shape)
            .translation(vector![0.0, 0.0, STANDING_HALF_HEIGHT + CAPSULE_RADIUS])
            .enabled(true)
            .build();
        let standing_collider_handle =
            colliders.insert_with_parent(standing_collider, player_body_handle, &mut bodies);

        // Crouching capsule (Z-axis)
        let crouch_shape = SharedShape::capsule_z(CROUCH_HALF_HEIGHT, CAPSULE_RADIUS);
        let crouch_collider = ColliderBuilder::new(crouch_shape)
            .translation(vector![0.0, 0.0, CROUCH_HALF_HEIGHT + CAPSULE_RADIUS])
            .enabled(false)
            .build();
        let crouch_collider_handle =
            colliders.insert_with_parent(crouch_collider, player_body_handle, &mut bodies);

        let mut query_pipeline = QueryPipeline::new();
        query_pipeline.update(&colliders);

        Self {
            bodies,
            colliders,
            query_pipeline,
            character_controller,
            player_body_handle,
            standing_collider_handle,
            crouch_collider_handle,
            is_crouched: false,
            player_stamina: MAX_STAMINA,
            player_sliding: false,
            player_slide_time: 0.0,
            player_sprinting: false,
        }
    }

    pub fn set_position(&mut self, x: f32, y: f32, z: f32) {
        if let Some(body) = self.bodies.get_mut(self.player_body_handle) {
            body.set_translation(vector![x, y, z], true);
            self.query_pipeline.update(&self.colliders);
        }
    }

    pub fn get_position(&self) -> (f32, f32, f32) {
        if let Some(body) = self.bodies.get(self.player_body_handle) {
            let t = body.translation();
            (t.x, t.y, t.z)
        } else {
            (0.0, 0.0, 0.0)
        }
    }

    pub fn step(&mut self, state: &mut RapierPlayerState, input: RapierMoveInput) {
        let dt = input.dt.min(0.05);

        // Stamina calculation
        let stamina = if state.stamina <= 0.0 && !state.sprinting {
            MAX_STAMINA
        } else {
            state.stamina
        };
        let is_sprinting = input.sprint && input.forward > 0.1 && state.grounded && !self.is_crouched && stamina > 0.0;
        state.sprinting = is_sprinting;
        state.stamina = if is_sprinting {
            (stamina - STAMINA_DRAIN * dt).max(0.0)
        } else {
            (stamina + STAMINA_RECOVERY * dt).min(MAX_STAMINA)
        };

        let h_speed = (state.vx * state.vx + state.vy * state.vy).sqrt();

        // Crouch Power-Slide & Slide Cancel
        if input.crouch && state.grounded && !state.sliding && (is_sprinting || h_speed > 6.0) {
            state.sliding = true;
            state.slide_time = 0.0;
            let boost = (SLIDE_INITIAL_SPEED / h_speed.max(0.1)).max(1.15);
            let target_speed = (h_speed * boost).min(14.0);
            let scale = target_speed / h_speed.max(0.1);
            state.vx *= scale;
            state.vy *= scale;
        }

        let mut want_crouch = input.crouch;
        if state.sliding {
            state.slide_time += dt;
            if input.jump {
                // Slide cancel into full speed jump!
                state.sliding = false;
                state.vz = JUMP_VELOCITY;
                state.grounded = false;
            } else {
                want_crouch = true;
                let cur_speed = (state.vx * state.vx + state.vy * state.vy).sqrt();
                if state.slide_time >= MAX_SLIDE_TIME || cur_speed < 3.5 || !input.crouch {
                    state.sliding = false;
                }
            }
        }

        // Handle Crouch Transition
        if want_crouch != self.is_crouched {
            if want_crouch {
                self.is_crouched = true;
                if let Some(sc) = self.colliders.get_mut(self.standing_collider_handle) {
                    sc.set_enabled(false);
                }
                if let Some(cc) = self.colliders.get_mut(self.crouch_collider_handle) {
                    cc.set_enabled(true);
                }
            } else if self.check_headroom() {
                self.is_crouched = false;
                if let Some(cc) = self.colliders.get_mut(self.crouch_collider_handle) {
                    cc.set_enabled(false);
                }
                if let Some(sc) = self.colliders.get_mut(self.standing_collider_handle) {
                    sc.set_enabled(true);
                }
            }
        }

        // Direction from yaw and inputs
        let rad = input.yaw.to_radians();
        let forward_x = -rad.sin();
        let forward_y = rad.cos();
        let right_x = rad.cos();
        let right_y = rad.sin();

        let mut wish_x = forward_x * input.forward + right_x * input.strafe;
        let mut wish_y = forward_y * input.forward + right_y * input.strafe;
        let len = (wish_x * wish_x + wish_y * wish_y).sqrt();
        if len > 0.001 {
            wish_x /= len;
            wish_y /= len;
        }

        let mut speed = if self.is_crouched {
            CROUCH_SPEED
        } else if is_sprinting {
            SPRINT_SPEED
        } else {
            RUN_SPEED
        };
        if input.knife {
            speed *= 1.10;
        }

        // Horizontal acceleration / friction / air strafe
        if !state.grounded {
            if input.strafe.abs() > 0.1 {
                let air_strafe_accel = 18.0;
                state.vx += right_x * input.strafe * air_strafe_accel * dt;
                state.vy += right_y * input.strafe * air_strafe_accel * dt;
                let cur_air = (state.vx * state.vx + state.vy * state.vy).sqrt();
                if cur_air > 15.0 {
                    state.vx = (state.vx / cur_air) * 15.0;
                    state.vy = (state.vy / cur_air) * 15.0;
                }
            } else {
                state.vx += (wish_x * speed - state.vx) * (2.5 * dt).min(1.0);
                state.vy += (wish_y * speed - state.vy) * (2.5 * dt).min(1.0);
            }
        } else if !state.sliding {
            let accel_rate = if input.knife { 12.0 * 1.10 } else { 12.0 };
            state.vx += (wish_x * speed - state.vx) * (accel_rate * dt).min(1.0);
            state.vy += (wish_y * speed - state.vy) * (accel_rate * dt).min(1.0);
        } else {
            let slide_friction = 3.5;
            state.vx -= state.vx * (slide_friction * dt).min(1.0);
            state.vy -= state.vy * (slide_friction * dt).min(1.0);
        }

        // Jump & Gravity
        if input.jump && state.grounded {
            state.vz = JUMP_VELOCITY;
            state.grounded = false;
        } else if !state.grounded {
            state.vz += GRAVITY * dt;
        }

        let desired_translation = vector![state.vx * dt, state.vy * dt, state.vz * dt];

        // Active shape for movement calculation
        let active_shape = if self.is_crouched {
            SharedShape::capsule_z(CROUCH_HALF_HEIGHT, CAPSULE_RADIUS)
        } else {
            SharedShape::capsule_z(STANDING_HALF_HEIGHT, CAPSULE_RADIUS)
        };

        let current_pos = self.bodies[self.player_body_handle].position();
        let filter = QueryFilter::default().exclude_rigid_body(self.player_body_handle);

        let mut ground_normal = vector![0.0, 0.0, 1.0];
        let mut has_ground_contact = false;
        let collisions = self.character_controller.move_shape(
            dt,
            &self.bodies,
            &self.colliders,
            &self.query_pipeline,
            &*active_shape,
            current_pos,
            desired_translation,
            filter,
            |collision| {
                if collision.hit.normal1.z > 0.7 {
                    has_ground_contact = true;
                    ground_normal = collision.hit.normal1.into_inner();
                }
            },
        );

        if state.sliding && has_ground_contact {
            let cur_speed = (state.vx * state.vx + state.vy * state.vy).sqrt();
            if cur_speed > 0.1 {
                let dir_x = state.vx / cur_speed;
                let dir_y = state.vy / cur_speed;
                let downhill_dot = dir_x * (-ground_normal.x) + dir_y * (-ground_normal.y);
                let slope_sin = (ground_normal.x * ground_normal.x + ground_normal.y * ground_normal.y).sqrt();
                if downhill_dot > 0.05 && slope_sin > 0.05 {
                    let downhill_accel = GRAVITY.abs() * slope_sin * downhill_dot * 1.5;
                    state.vx += dir_x * downhill_accel * dt;
                    state.vy += dir_y * downhill_accel * dt;
                }
            }
        }

        let new_pos = current_pos.translation.vector + collisions.translation;
        if let Some(body) = self.bodies.get_mut(self.player_body_handle) {
            body.set_translation(new_pos, true);
        }

        let was_grounded = state.grounded;
        if state.vz > 0.0 {
            state.grounded = false;
        } else {
            state.grounded = collisions.grounded || has_ground_contact;
            if state.grounded && !was_grounded {
                state.vz = 0.0;
            }
        }

        state.x = new_pos.x;
        state.y = new_pos.y;
        state.z = new_pos.z;
        state.crouching = self.is_crouched;

        self.query_pipeline.update(&self.colliders);
    }

    /// Step the native PlayerState directly with Rapier3D kinematic character controller.
    pub fn step_player(
        &mut self,
        player: &mut crate::physics::PlayerState,
        input: crate::physics::MoveInput,
        dt: f32,
    ) {
        let dt = dt.min(0.05);
        if dt <= 0.0 {
            return;
        }

        player.t += dt;
        player.fall_speed = 0.0;

        // Stamina calculation
        let is_sprinting = input.sprint && input.forward > 0.1 && player.on_ground && !self.is_crouched && self.player_stamina > 0.0;
        self.player_sprinting = is_sprinting;
        self.player_stamina = if is_sprinting {
            (self.player_stamina - STAMINA_DRAIN * dt).max(0.0)
        } else {
            (self.player_stamina + STAMINA_RECOVERY * dt).min(MAX_STAMINA)
        };

        let h_speed = (player.vel_x * player.vel_x + player.vel_y * player.vel_y).sqrt();

        // Crouch Power-Slide & Slide-Canceling
        if input.crouch && player.on_ground && !self.player_sliding && (is_sprinting || h_speed > 6.0) {
            self.player_sliding = true;
            self.player_slide_time = 0.0;
            let boost = (SLIDE_INITIAL_SPEED / h_speed.max(0.1)).max(1.15);
            let target_speed = (h_speed * boost).min(14.0);
            let scale = target_speed / h_speed.max(0.1);
            player.vel_x *= scale;
            player.vel_y *= scale;
        }

        let mut want_crouch = input.crouch;
        if self.player_sliding {
            self.player_slide_time += dt;
            if input.jump {
                // Slide cancel into full speed jump!
                self.player_sliding = false;
                player.vel_z = JUMP_VELOCITY;
                player.on_ground = false;
            } else {
                want_crouch = true;
                let cur_speed = (player.vel_x * player.vel_x + player.vel_y * player.vel_y).sqrt();
                if self.player_slide_time >= MAX_SLIDE_TIME || cur_speed < 3.5 || !input.crouch {
                    self.player_sliding = false;
                }
            }
        }

        // Crouch transition
        if want_crouch != self.is_crouched {
            if want_crouch {
                self.is_crouched = true;
                if let Some(sc) = self.colliders.get_mut(self.standing_collider_handle) {
                    sc.set_enabled(false);
                }
                if let Some(cc) = self.colliders.get_mut(self.crouch_collider_handle) {
                    cc.set_enabled(true);
                }
            } else if self.check_headroom() {
                self.is_crouched = false;
                if let Some(cc) = self.colliders.get_mut(self.crouch_collider_handle) {
                    cc.set_enabled(false);
                }
                if let Some(sc) = self.colliders.get_mut(self.standing_collider_handle) {
                    sc.set_enabled(true);
                }
            }
        }
        player.crouch = if self.is_crouched { 1.0 } else { 0.0 };
        player.crouch_held = want_crouch;

        // Direction from yaw (yaw is in radians: cos is X, sin is Y)
        let mut wish_x = player.yaw.cos() * input.forward - player.yaw.sin() * input.strafe;
        let mut wish_y = player.yaw.sin() * input.forward + player.yaw.cos() * input.strafe;
        let len = (wish_x * wish_x + wish_y * wish_y).sqrt();
        if len > 0.001 {
            wish_x /= len;
            wish_y /= len;
        }

        let mut speed = if self.is_crouched {
            CROUCH_SPEED
        } else if is_sprinting {
            SPRINT_SPEED
        } else {
            RUN_SPEED
        };
        if input.knife {
            speed *= 1.10;
        }

        // Horizontal acceleration / friction / air strafe
        if !player.on_ground {
            if input.strafe.abs() > 0.1 {
                let air_strafe_accel = 18.0;
                // Right vector in world: (-sin(yaw), cos(yaw))
                player.vel_x += -player.yaw.sin() * input.strafe * air_strafe_accel * dt;
                player.vel_y += player.yaw.cos() * input.strafe * air_strafe_accel * dt;
                let cur_air = (player.vel_x * player.vel_x + player.vel_y * player.vel_y).sqrt();
                if cur_air > 15.0 {
                    player.vel_x = (player.vel_x / cur_air) * 15.0;
                    player.vel_y = (player.vel_y / cur_air) * 15.0;
                }
            } else {
                player.vel_x += (wish_x * speed - player.vel_x) * (2.5 * dt).min(1.0);
                player.vel_y += (wish_y * speed - player.vel_y) * (2.5 * dt).min(1.0);
            }
        } else if !self.player_sliding {
            let accel_rate = if input.knife { 12.0 * 1.10 } else { 12.0 };
            player.vel_x += (wish_x * speed - player.vel_x) * (accel_rate * dt).min(1.0);
            player.vel_y += (wish_y * speed - player.vel_y) * (accel_rate * dt).min(1.0);
        } else {
            let slide_friction = 3.5;
            player.vel_x -= player.vel_x * (slide_friction * dt).min(1.0);
            player.vel_y -= player.vel_y * (slide_friction * dt).min(1.0);
        }

        // Bunny-hop landing grace period (80ms timing window)
        let bhop_timing = player.on_ground && input.jump && (player.t - player.landed_at <= 0.08);

        if (input.jump && player.on_ground) || bhop_timing {
            player.vel_z = JUMP_VELOCITY;
            player.on_ground = false;
        } else if !player.on_ground {
            player.vel_z += GRAVITY * dt;
        }

        let desired_z = if player.on_ground && player.vel_z <= 0.0 {
            -0.05
        } else {
            player.vel_z * dt
        };

        let desired_translation = vector![player.vel_x * dt, player.vel_y * dt, desired_z];

        let offset_z = if self.is_crouched {
            CROUCH_HALF_HEIGHT + CAPSULE_RADIUS
        } else {
            STANDING_HALF_HEIGHT + CAPSULE_RADIUS
        };

        let active_shape = if self.is_crouched {
            SharedShape::capsule_z(CROUCH_HALF_HEIGHT, CAPSULE_RADIUS)
        } else {
            SharedShape::capsule_z(STANDING_HALF_HEIGHT, CAPSULE_RADIUS)
        };

        let current_pos = self.bodies[self.player_body_handle].position();
        let mut shape_pos = *current_pos;
        shape_pos.translation.vector.z += offset_z;

        let filter = QueryFilter::default().exclude_rigid_body(self.player_body_handle);

        let mut has_ground_contact = false;
        let mut ground_normal = vector![0.0, 0.0, 1.0];
        let collisions = self.character_controller.move_shape(
            dt,
            &self.bodies,
            &self.colliders,
            &self.query_pipeline,
            &*active_shape,
            &shape_pos,
            desired_translation,
            filter,
            |collision| {
                if collision.hit.normal1.z > 0.7 {
                    has_ground_contact = true;
                    ground_normal = collision.hit.normal1.into_inner();
                }
            },
        );

        if self.player_sliding && has_ground_contact {
            let cur_speed = (player.vel_x * player.vel_x + player.vel_y * player.vel_y).sqrt();
            if cur_speed > 0.1 {
                let dir_x = player.vel_x / cur_speed;
                let dir_y = player.vel_y / cur_speed;
                let downhill_dot = dir_x * (-ground_normal.x) + dir_y * (-ground_normal.y);
                let slope_sin = (ground_normal.x * ground_normal.x + ground_normal.y * ground_normal.y).sqrt();
                if downhill_dot > 0.05 && slope_sin > 0.05 {
                    let downhill_accel = GRAVITY.abs() * slope_sin * downhill_dot * 1.5;
                    player.vel_x += dir_x * downhill_accel * dt;
                    player.vel_y += dir_y * downhill_accel * dt;
                }
            }
        }

        let new_pos = current_pos.translation.vector + collisions.translation;
        if let Some(body) = self.bodies.get_mut(self.player_body_handle) {
            body.set_translation(new_pos, true);
        }

        let was_grounded = player.on_ground;
        if player.vel_z > 0.0 {
            player.on_ground = false;
        } else {
            player.on_ground = collisions.grounded || has_ground_contact;
            if player.on_ground && !was_grounded {
                player.fall_speed = player.vel_z.abs();
                player.vel_z = 0.0;
                player.landed_at = player.t;
            }
        }

        player.x = new_pos.x;
        player.y = new_pos.y;
        player.z = new_pos.z;

        self.query_pipeline.update(&self.colliders);
    }

    fn check_headroom(&self) -> bool {
        let pos = self.bodies[self.player_body_handle].translation();
        let ray = Ray::new(
            point![pos.x, pos.y, pos.z + CROUCH_HALF_HEIGHT],
            vector![0.0, 0.0, 1.0],
        );
        let filter = QueryFilter::default().exclude_rigid_body(self.player_body_handle);
        self.query_pipeline
            .cast_ray(&self.bodies, &self.colliders, &ray, 0.7, true, filter)
            .is_none()
    }

    pub fn cast_ray(
        &self,
        origin: [f32; 3],
        dir: [f32; 3],
        max_dist: f32,
    ) -> (bool, f32, [f32; 3]) {
        let ray = Ray::new(
            point![origin[0], origin[1], origin[2]],
            vector![dir[0], dir[1], dir[2]],
        );
        let filter = QueryFilter::default().exclude_rigid_body(self.player_body_handle);

        if let Some((_, hit)) = self.query_pipeline.cast_ray_and_get_normal(
            &self.bodies,
            &self.colliders,
            &ray,
            max_dist,
            true,
            filter,
        ) {
            let hit_point = ray.point_at(hit.time_of_impact);
            (true, hit.time_of_impact, [hit_point.x, hit_point.y, hit_point.z])
        } else {
            (
                false,
                max_dist,
                [
                    origin[0] + dir[0] * max_dist,
                    origin[1] + dir[1] * max_dist,
                    origin[2] + dir[2] * max_dist,
                ],
            )
        }
    }
}

//! The developer console, as the native client's half of it.
//!
//! **The registry is served, not duplicated.** `GET /api/hassault/console/definitions`
//! returns every CVar, ConCommand and macro the node knows about, and this
//! module renders that list rather than declaring one of its own. A Rust copy of
//! the CVar table would be a third source of truth after `console.py` and
//! `registry.ts`, and the failure mode of a third copy is not a build error —
//! it is a console that offers a command the server has never heard of.
//!
//! Execution splits exactly where the flags say it does:
//!
//! - A CVar flagged **`client`** is *ours*. The server deliberately does not
//!   apply it — `draw.hitboxes` has no meaning on a machine with no screen — so
//!   it is set locally and never touches the wire.
//! - Everything else goes to the node over the **match socket**, as
//!   `console_exec`. `channel.py` resolves the room and the player from the
//!   connection itself, so a command sent this way lands in the match this
//!   client is in; the REST route the browser pane uses has to be *told* which
//!   room, and can be told the wrong one.
//! - `bind`, `unbind`, `alias` and `clear` never leave the client at all, the
//!   same four the browser's `executor.ts` intercepts.
//!
//! ## The honesty rule
//!
//! The node serves 22 `client`-flagged CVars and this client reads seven of them.
//! The tempting behaviour is to accept every assignment silently, which is
//! precisely the failure this whole change exists to end: a console that answers
//! `net.prediction = false` while prediction carries on running is worse than
//! one that refuses, because it has now told the player something untrue.
//!
//! So `HONORED` names what this client actually acts on, a set anything can be
//! added to the moment a reader for it exists, and setting anything else stores
//! the value and says plainly that nothing is reading it. The console becomes
//! the place divergence is *visible* rather than another place it hides.
//!
//! ## What the browser has, and how it lands here
//!
//! The pane's console is a React panel with a toolbar, filter tabs and a macro
//! drawer. This one is drawn by `hud.rs` out of 5x7 glyphs while the mouse is
//! aiming a weapon, so the same capabilities arrive by a different route — but
//! they do all arrive, because the useful half of a toolbar button is usually
//! not the click:
//!
//! | Browser | Here |
//! | --- | --- |
//! | Title bar: room, map, ping, cheats | The header line |
//! | Quick-toggle buttons | `quick_actions`, on `F1`-`F8`, drawn as state chips |
//! | Filter tabs | `Filter`, on `^F` or `filter <name>` |
//! | Macro drawer | `macros`, listing what the node serves |
//! | Copy logs | `copy`, which writes a file — see `Dispatch::SaveTranscript` |
//! | Autocomplete rows carrying a type and a description | `suggestion_detail`, under the input |
//! | Timestamps | `LogLine::at`, as elapsed rather than wall clock |
//!
//! Two of those are more honest here than in the browser, and deliberately so.
//! A chip for something this client does not read is drawn **as unhonored**
//! rather than hidden, and a server-owned value nobody has told us yet draws as
//! `?` rather than as `OFF`.

use std::collections::{BTreeMap, HashMap, VecDeque};
use std::time::Instant;

use serde::Deserialize;

use crate::divergence;

/// The most lines kept in the scrollback. Old lines are dropped from the front:
/// a console that grew without bound would be a memory leak with a UI.
const MAX_LOG: usize = 400;
/// The most lines kept in the input history.
const MAX_HISTORY: usize = 100;

/// Client CVars this build actually reads.
///
/// **Adding a name here is a promise**, and the promise is checked by a person,
/// not a test — there is no way to assert from here that `app.rs` consults a
/// map key. What the list does buy is that the *absence* of a name is reported
/// (see `divergence::note_unhonored_cvar`), so the failure mode is a console
/// that admits what it is ignoring rather than one that pretends.
///
/// Kept sorted, and kept honest: a name belongs here when something reads it,
/// not when someone intends to write the reader.
pub const HONORED: &[&str] = &[
    "draw.crosshair.gap",
    "draw.crosshair.size",
    "draw.crosshair.style",
    "draw.crosshair.thickness",
    "draw.hitboxes",
    "net.graph",
    "player.sensitivity",
];

// -----------------------------------------------------------------------------
// The served registry
// -----------------------------------------------------------------------------

/// Mirrors `console.CVarDefinition`. Unknown fields are ignored rather than
/// refused — this is a read of a document the server owns, and a client that
/// failed to parse the registry because it grew a field would lose the whole
/// console over one key.
#[derive(Debug, Clone, Deserialize, Default)]
pub struct CVarDef {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub namespace: String,
    #[serde(rename = "type", default)]
    pub kind: String,
    #[serde(default)]
    pub default_value: serde_json::Value,
    #[serde(default)]
    pub current_value: serde_json::Value,
    #[serde(default)]
    pub min_value: Option<f64>,
    #[serde(default)]
    pub max_value: Option<f64>,
    #[serde(default)]
    pub enum_values: Option<Vec<String>>,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub flags: Vec<String>,
}

impl CVarDef {
    /// Whether this CVar is the client's to apply.
    ///
    /// Read off the served flags rather than off a namespace: `draw.*` happens
    /// to be all-client today, and inferring the rule from that coincidence
    /// would send the first server-side `draw.*` CVar down the wrong path.
    pub fn is_client(&self) -> bool {
        self.flags.iter().any(|f| f == "client")
    }

    /// Coerce a typed value out of what the user typed, refusing rather than
    /// guessing.
    ///
    /// `min`/`max` clamp instead of refusing, matching the browser registry's
    /// `set`: a sensitivity of 50 is a typo with an obvious intent, whereas
    /// `draw.hitboxes purple` has none.
    pub fn coerce(&self, raw: &str) -> Result<serde_json::Value, String> {
        let text = raw.trim().trim_matches('"').trim_matches('\'');
        match self.kind.as_str() {
            "boolean" => match text.to_ascii_lowercase().as_str() {
                "1" | "true" | "on" | "yes" => Ok(serde_json::Value::Bool(true)),
                "0" | "false" | "off" | "no" => Ok(serde_json::Value::Bool(false)),
                _ => Err(format!("'{text}' is not a boolean (try 0 or 1)")),
            },
            "number" => {
                let mut n: f64 = text
                    .parse()
                    .map_err(|_| format!("'{text}' is not a number"))?;
                if let Some(min) = self.min_value {
                    n = n.max(min);
                }
                if let Some(max) = self.max_value {
                    n = n.min(max);
                }
                serde_json::Number::from_f64(n)
                    .map(serde_json::Value::Number)
                    // NaN and the infinities have no JSON spelling, and a CVar
                    // silently becoming `null` is worse than a refusal.
                    .ok_or_else(|| format!("'{text}' is not a finite number"))
            }
            "enum" => match &self.enum_values {
                Some(allowed) if !allowed.iter().any(|v| v.eq_ignore_ascii_case(text)) => {
                    Err(format!("'{text}' is not one of: {}", allowed.join(", ")))
                }
                _ => Ok(serde_json::Value::String(text.to_string())),
            },
            // Includes "string" and anything a newer server invents: passing the
            // text through is the one coercion that cannot be wrong.
            _ => Ok(serde_json::Value::String(text.to_string())),
        }
    }
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct ConCommandDef {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub description: String,
    #[serde(default)]
    pub signature: String,
    #[serde(default)]
    pub example: String,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct MacroDef {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub description: String,
}

/// The whole served registry, as one read.
#[derive(Debug, Clone, Deserialize, Default)]
pub struct Definitions {
    #[serde(default)]
    pub cvars: Vec<CVarDef>,
    #[serde(default)]
    pub commands: Vec<ConCommandDef>,
    #[serde(default)]
    pub macros: Vec<MacroDef>,
}

// -----------------------------------------------------------------------------
// Client CVar state
// -----------------------------------------------------------------------------

/// The client's current values for `client`-flagged CVars.
///
/// **Overrides, not a mirror.** The map is empty until something is set, and
/// every reader passes the value it would otherwise have used as the fallback:
///
/// ```ignore
/// let graph = cvars.number("net.graph").unwrap_or(settings_default);
/// ```
///
/// That shape is deliberate. Seeding the map from the served defaults would make
/// a node that cannot answer `/console/definitions` — an older backend, a
/// network hiccup at boot — into a client with *no* settings at all, because the
/// defaults it was going to read never arrived. As overrides, a failed fetch
/// costs autocomplete and validation and changes no behaviour whatsoever.
#[derive(Debug, Default, Clone)]
pub struct ClientCvars {
    values: HashMap<String, serde_json::Value>,
}

impl ClientCvars {
    pub fn set(&mut self, name: &str, value: serde_json::Value) {
        if !HONORED.contains(&name) {
            divergence::note_unhonored_cvar(name);
        }
        self.values.insert(name.to_string(), value);
    }

    pub fn get(&self, name: &str) -> Option<&serde_json::Value> {
        self.values.get(name)
    }

    pub fn number(&self, name: &str) -> Option<f32> {
        self.values.get(name)?.as_f64().map(|v| v as f32)
    }

    /// A boolean CVar, accepting the number a script may have set it to.
    ///
    /// `server.cheats = True` in a macro comes back through `affectedCvars` as a
    /// real bool, but a user typing `draw.hitboxes 1` produces a number whenever
    /// the server declares that CVar as `number`. Reading only `as_bool` would
    /// make the console's own macros work and the user's typing not.
    pub fn boolean(&self, name: &str) -> Option<bool> {
        match self.values.get(name)? {
            serde_json::Value::Bool(b) => Some(*b),
            serde_json::Value::Number(n) => Some(n.as_f64().unwrap_or(0.0) != 0.0),
            _ => None,
        }
    }

    pub fn string(&self, name: &str) -> Option<&str> {
        self.values.get(name)?.as_str()
    }

    /// Whether this client reads the named CVar. See `HONORED`.
    pub fn is_honored(name: &str) -> bool {
        HONORED.contains(&name)
    }
}

// -----------------------------------------------------------------------------
// The console itself
// -----------------------------------------------------------------------------

/// How a log line should be coloured. The console prints three kinds of thing
/// and they must be distinguishable at a glance: what you typed, what came back,
/// and what went wrong.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tone {
    Echo,
    Output,
    Error,
    Note,
}

/// Which of the console's subject areas a line belongs to.
///
/// A **bitmask, not a single value**, because the categories genuinely overlap —
/// `server.bots.add` is about the server *and* about bots — and forcing a line
/// into one bucket would make it vanish from a filter it plainly belongs in.
/// The browser evaluates the same substring rules per line per render; here they
/// are evaluated **once, when the line is pushed**, because this console is
/// repainted sixty times a second and lowercasing four hundred strings a frame
/// to decide what to draw is the kind of cost that only shows up as "the game
/// stutters while the console is open".
pub mod channel {
    pub const NET: u8 = 1 << 0;
    pub const DRAW: u8 = 1 << 1;
    pub const SERVER: u8 = 1 << 2;
    pub const MACRO: u8 = 1 << 3;
}

/// Classify a line, by the same rules `DeveloperConsole.tsx` filters with.
///
/// Deliberately a copy of the browser's heuristics rather than an improvement on
/// them: the point of the filter is that the same line lands in the same tab in
/// both clients, and a native console that were *cleverer* about it would sort
/// the same output differently, which is worse than either rule alone.
fn classify(text: &str) -> u8 {
    let lower = text.to_ascii_lowercase();
    let mut mask = 0;
    if lower.contains("net.") || lower.contains("ping") {
        mask |= channel::NET;
    }
    if lower.contains("draw.") || lower.contains("hitbox") {
        mask |= channel::DRAW;
    }
    if lower.contains("server.") || lower.contains("bot") {
        mask |= channel::SERVER;
    }
    if lower.contains("macro.") || lower.contains("[macro]") {
        mask |= channel::MACRO;
    }
    mask
}

/// Which lines the scrollback is showing.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Filter {
    #[default]
    All,
    Net,
    Draw,
    Server,
    Macro,
}

impl Filter {
    pub const ALL: [Filter; 5] = [
        Filter::All,
        Filter::Net,
        Filter::Draw,
        Filter::Server,
        Filter::Macro,
    ];

    pub fn label(self) -> &'static str {
        match self {
            Filter::All => "ALL",
            Filter::Net => "NET",
            Filter::Draw => "DRAW",
            Filter::Server => "SERVER",
            Filter::Macro => "MACRO",
        }
    }

    fn mask(self) -> u8 {
        match self {
            Filter::All => 0,
            Filter::Net => channel::NET,
            Filter::Draw => channel::DRAW,
            Filter::Server => channel::SERVER,
            Filter::Macro => channel::MACRO,
        }
    }

    fn parse(name: &str) -> Option<Filter> {
        Filter::ALL
            .into_iter()
            .find(|f| f.label().eq_ignore_ascii_case(name))
    }

    fn next(self) -> Filter {
        let i = Filter::ALL.iter().position(|f| *f == self).unwrap_or(0);
        Filter::ALL[(i + 1) % Filter::ALL.len()]
    }
}

#[derive(Debug, Clone)]
pub struct LogLine {
    pub text: String,
    pub tone: Tone,
    /// Seconds since the client started.
    ///
    /// **Elapsed, not a wall clock**, and that is a choice rather than a
    /// shortcut. `std` cannot render a local time — there is no timezone
    /// database in it — so a wall clock here would mean either a dependency
    /// carrying one or printing UTC and letting the reader do arithmetic. What a
    /// console log is actually read for is the *interval* between two lines: how
    /// long after the join the first correction came, how far apart two macro
    /// runs were. Elapsed answers that directly and cannot be wrong about a
    /// timezone.
    pub at: f32,
    /// Which filters show this line. See `channel`.
    pub channels: u8,
}

impl LogLine {
    fn visible_under(&self, filter: Filter) -> bool {
        let mask = filter.mask();
        mask == 0 || self.channels & mask != 0
    }

    /// `MM:SS`, as the scrollback prints it.
    pub fn stamp(&self) -> String {
        let total = self.at.max(0.0) as u32;
        format!("{:02}:{:02}", total / 60, total % 60)
    }
}

/// What one executed line asks the caller to do.
///
/// The console never touches the socket itself. It is a pure state machine over
/// text, which is what makes every branch below testable without a server, a
/// window or a GPU.
#[derive(Debug, Clone, PartialEq)]
pub enum Dispatch {
    /// Handled locally; nothing to send.
    Handled,
    /// Send this line to the node as `console_exec` with this request id.
    Send { command: String, req_id: u64 },
    /// Write the scrollback somewhere the player can get at it, and say where.
    ///
    /// The browser's equivalent button copies to the clipboard. This client has
    /// no clipboard and deliberately does not grow one for a single console
    /// action: the crates that provide one pull an X11/Wayland stack in on Linux
    /// and own a background thread for the lifetime of the process, which is a
    /// lot of surface area for a convenience. A file is also the more useful
    /// artifact natively — it can be attached to a bug report, and it survives
    /// the crash you were trying to capture.
    SaveTranscript { text: String },
}

#[derive(Default)]
pub struct Console {
    pub open: bool,
    pub input: String,
    /// Byte index into `input`. A byte index rather than a character one because
    /// every edit below splices the `String` directly, and the two only agree
    /// while the input is ASCII — which is exactly the assumption that breaks on
    /// the first player who types a name with an accent in it.
    pub cursor: usize,
    log: VecDeque<LogLine>,
    history: Vec<String>,
    history_index: usize,
    /// How far back through the log the view is scrolled, in lines from the
    /// bottom. Reset on every new line, because a console that stayed scrolled
    /// up would hide the answer to the command just typed.
    pub scroll: usize,
    defs: Definitions,
    /// `name -> def`, built once when the definitions land.
    cvar_index: BTreeMap<String, CVarDef>,
    aliases: HashMap<String, String>,
    binds: HashMap<String, String>,
    next_req: u64,
    /// Completions for the current input, and which one is selected.
    pub suggestions: Vec<String>,
    pub suggestion: usize,
    /// Which subject area the scrollback is showing. See `Filter`.
    pub filter: Filter,
    /// When the first line was logged, for the elapsed stamps.
    ///
    /// `Option` so `Console` can keep deriving `Default` — an `Instant` has no
    /// default and never will, since a monotonic clock has no zero point. Set on
    /// the first push, which makes the first line `00:00` and every stamp after
    /// it an interval from the console coming up.
    started: Option<Instant>,
    /// The last value **seen** for each server-owned CVar.
    ///
    /// Seeded from the registry's `current_value` at load and updated from every
    /// answer's `affectedCvars`. It is a cache of what was last observed and not
    /// a source of truth — the server owns these, and another player's macro can
    /// move one without this client hearing about it. The browser's registry is
    /// exactly the same thing, and it is why the status chips are labelled as
    /// the last known state rather than as a live read.
    server_view: BTreeMap<String, serde_json::Value>,
}

impl Console {
    /// Install the served registry. Called once, when the fetch lands.
    pub fn set_definitions(&mut self, defs: Definitions) {
        self.cvar_index = defs
            .cvars
            .iter()
            .map(|c| (c.name.clone(), c.clone()))
            .collect();
        // Seed the last-known server values. Only the server-owned ones: a
        // client CVar's authority is `ClientCvars`, and a second copy here would
        // be a value the chips could disagree with the console about.
        //
        // A **null** `current_value` is skipped rather than stored. The field is
        // `#[serde(default)]`, so a node that does not report one leaves
        // `Value::Null` there — and a null in this map reads as `Some(false)`
        // to every caller, which turns "never been told" into a confident
        // "off". That is precisely the claim this module refuses to make, and
        // it is invisible: the chip simply draws OFF and is believed.
        self.server_view = self
            .cvar_index
            .values()
            .filter(|c| !c.is_client() && !c.current_value.is_null())
            .map(|c| (c.name.clone(), c.current_value.clone()))
            .collect();
        let (cvars, commands, macros) = (defs.cvars.len(), defs.commands.len(), defs.macros.len());
        self.defs = defs;
        self.note(format!(
            "registry: {cvars} cvars, {commands} commands, {macros} macros"
        ));
        // Said at load rather than at first use: the gap between what the node
        // offers and what this client reads is the thing worth knowing *before*
        // you rely on one of them mid-match.
        let unread = self.unread_cvars().len();
        if unread > 0 {
            self.note(format!(
                "{unread} client cvars have no reader here - type DIVERGENCE"
            ));
        }
    }

    pub fn definitions(&self) -> &Definitions {
        &self.defs
    }

    pub fn lines(&self) -> &VecDeque<LogLine> {
        &self.log
    }

    /// Client CVars the node serves that nothing here reads.
    pub fn unread_cvars(&self) -> Vec<String> {
        self.defs
            .cvars
            .iter()
            .filter(|c| c.is_client() && !ClientCvars::is_honored(&c.name))
            .map(|c| c.name.clone())
            .collect()
    }

    pub fn push(&mut self, text: impl Into<String>, tone: Tone) {
        // Split on newlines here rather than at every call site: server output
        // arrives as whole paragraphs, and a painter that had to wrap would need
        // to know the font.
        let at = self.elapsed();
        for line in text.into().split('\n') {
            self.log.push_back(LogLine {
                channels: classify(line),
                text: line.to_string(),
                tone,
                at,
            });
        }
        while self.log.len() > MAX_LOG {
            self.log.pop_front();
        }
        self.scroll = 0;
    }

    /// Seconds since the first line was logged, starting the clock if needed.
    fn elapsed(&mut self) -> f32 {
        self.started
            .get_or_insert_with(Instant::now)
            .elapsed()
            .as_secs_f32()
    }

    /// The lines the current filter shows, oldest first.
    ///
    /// Borrows rather than copies: a scrollback is up to 400 lines and this is
    /// called once a frame while the console is open.
    pub fn visible(&self) -> Vec<&LogLine> {
        self.log
            .iter()
            .filter(|l| l.visible_under(self.filter))
            .collect()
    }

    /// How many lines the current filter is hiding. Drawn in the header, because
    /// a filtered console that did not say so looks like one that lost output.
    pub fn hidden_count(&self) -> usize {
        self.log
            .iter()
            .filter(|l| !l.visible_under(self.filter))
            .count()
    }

    /// Move to the next filter, and reset the scroll.
    ///
    /// The scroll reset is not tidiness: it counts lines back from the bottom of
    /// the *visible* set, so changing which lines are visible while keeping the
    /// offset silently scrolls you to an unrelated place in the log.
    pub fn cycle_filter(&mut self) {
        self.filter = self.filter.next();
        self.scroll = 0;
    }

    /// The whole scrollback as text, for `SaveTranscript`.
    ///
    /// **Unfiltered.** The filter is a reading aid; a transcript is evidence, and
    /// one that silently omitted three quarters of a session because a tab
    /// happened to be selected would be the worst possible thing to hand
    /// somebody debugging.
    pub fn transcript(&self) -> String {
        let mut out = String::new();
        for line in &self.log {
            out.push_str(&format!("[{}] {}\n", line.stamp(), line.text));
        }
        out
    }

    pub fn note(&mut self, text: impl Into<String>) {
        self.push(text, Tone::Note);
    }

    pub fn error(&mut self, text: impl Into<String>) {
        self.push(text, Tone::Error);
    }

    /// Fold the node's answer into the log and the local CVar state.
    ///
    /// `affected_cvars` is applied for **client** CVars only. A macro that sets
    /// `server.cheats` reports it here too, and writing that into `ClientCvars`
    /// would create a local copy of a value the server owns — one that goes
    /// stale the moment anything else changes it.
    pub fn on_response(&mut self, res: &crate::protocol::ConsoleResponse, cvars: &mut ClientCvars) {
        for line in &res.output {
            self.push(line.clone(), Tone::Output);
        }
        if let Some(err) = &res.error {
            if !err.is_empty() {
                self.error(err.clone());
            }
        }
        let applied: Vec<(String, serde_json::Value)> = res
            .affected_cvars
            .iter()
            .filter(|(name, _)| {
                self.cvar_index
                    .get(*name)
                    .map(|d| d.is_client())
                    .unwrap_or(false)
            })
            .map(|(name, value)| (name.clone(), value.clone()))
            .collect();
        for (name, value) in applied {
            cvars.set(&name, value);
        }
        // The other half of the same message: everything the server *does* own,
        // remembered so the status chips have something to draw. Kept separate
        // from `ClientCvars` on purpose — see `server_view`.
        for (name, value) in &res.affected_cvars {
            match self.cvar_index.get(name) {
                Some(def) if !def.is_client() => {
                    self.server_view.insert(name.clone(), value.clone());
                }
                // A client cvar: already handled above, by `applied`.
                Some(_) => {}
                // No definition at all. Dropped — there is nothing to render an
                // untyped value as — but said out loud rather than silently.
                None => divergence::note_unknown_cvar(name),
            }
        }
    }

    /// The last value seen for a server-owned CVar. See `server_view`.
    pub fn server_value(&self, name: &str) -> Option<&serde_json::Value> {
        self.server_view.get(name)
    }

    /// The same, read as a flag. `None` means **never been told**, which is a
    /// different fact from `Some(false)` and is drawn differently.
    pub fn server_bool(&self, name: &str) -> Option<bool> {
        self.server_view.get(name).map(truthy)
    }
}

// -- quick actions --------------------------------------------------------

/// One entry in the row of quick actions, as the painter needs it.
///
/// The browser puts these on a toolbar you click. The mouse here is aiming a
/// weapon, so they are function keys instead — but the *state* half is the
/// part that earns its place either way: half of what a toggle button gives
/// you is not the click, it is being able to see at a glance that hitboxes
/// are on, which is otherwise a `draw.hitboxes` round trip.
pub struct QuickAction {
    /// The key that runs it, as the chip prints it.
    pub key: &'static str,
    pub label: &'static str,
    /// The current state, already rendered. `None` when nothing here knows
    /// it — a server CVar this client has never seen a value for.
    pub state: Option<String>,
    /// Whether the state reads as "on", for colouring the chip.
    pub active: bool,
    /// Whether this client actually acts on it. A chip for something only
    /// the browser reads is drawn, and drawn as unhonored, rather than
    /// hidden — see the honesty rule in the module docs.
    pub honored: bool,
    /// What pressing the key executes.
    pub command: String,
}

impl Console {
    /// The quick-action row: the browser's toolbar, as chips and keys.
    ///
    /// Built rather than declared as a table because every entry's command
    /// depends on the current state — a toggle has to know what it is
    /// toggling *from*, and a table of static strings would give you a key
    /// that always turns hitboxes on and never off.
    pub fn quick_actions(&self, cvars: &ClientCvars) -> Vec<QuickAction> {
        let toggle = |key, label, name: &str| {
            let on = cvars.boolean(name).unwrap_or_else(|| {
                self.cvar(name)
                    .map(|d| truthy(&d.default_value))
                    .unwrap_or(false)
            });
            QuickAction {
                key,
                label,
                state: Some(if on { "ON" } else { "OFF" }.to_string()),
                active: on,
                honored: ClientCvars::is_honored(name),
                command: format!("{name} {}", if on { 0 } else { 1 }),
            }
        };
        let god_on = self.server_view.get("player.god").map(truthy);
        let speed = self
            .server_view
            .get("server.timescale")
            .and_then(|v| v.as_f64())
            .unwrap_or(1.0);
        // 0.35 -> 1.0 -> 2.0 -> 0.35, the browser's three buttons as one
        // cycle. Compared with a tolerance rather than by equality: this
        // value has been through JSON, and `0.35f64` is not the number that
        // comes back out of every encoder.
        let next_speed = if (speed - 0.35).abs() < 0.01 {
            1.0
        } else if (speed - 1.0).abs() < 0.01 {
            2.0
        } else {
            0.35
        };
        vec![
            toggle("F1", "HITBOXES", "draw.hitboxes"),
            toggle("F2", "WIREFRAME", "draw.wireframe"),
            toggle("F3", "NETGRAPH", "net.graph"),
            QuickAction {
                key: "F4",
                label: "GOD",
                // `None`, not `OFF`. This client has never been told, and
                // drawing a state it does not know is the one thing the
                // whole module is against.
                state: god_on.map(|on| if on { "ON" } else { "OFF" }.to_string()),
                active: god_on.unwrap_or(false),
                honored: true,
                command: format!("player.god {}", if god_on.unwrap_or(false) { 0 } else { 1 }),
            },
            QuickAction {
                key: "F5",
                label: "+1 BOT",
                state: None,
                active: false,
                honored: true,
                command: "server.bots.add(count=1, skill=\"normal\")".to_string(),
            },
            QuickAction {
                key: "F6",
                label: "KICK BOTS",
                state: None,
                active: false,
                honored: true,
                command: "server.bots.kick_all()".to_string(),
            },
            QuickAction {
                key: "F7",
                label: "SPEED",
                state: Some(format!("{speed:.2}x")),
                active: (speed - 1.0).abs() >= 0.01,
                honored: true,
                command: format!("server.timescale {next_speed}"),
            },
            QuickAction {
                key: "F8",
                label: "WARMUP",
                state: None,
                active: false,
                honored: true,
                command: "macro.run(\"warmup\")".to_string(),
            },
        ]
    }

    /// What the nth quick action would run, if there is an nth.
    pub fn quick_command(&self, index: usize, cvars: &ClientCvars) -> Option<String> {
        self.quick_actions(cvars)
            .into_iter()
            .nth(index)
            .map(|q| q.command)
    }
}

impl Console {
    /// A one-line description of the completion currently selected.
    ///
    /// The browser's autocomplete list carries a type and a description on every
    /// row. A row here is one cell of a single line of 5x7 glyphs, so the
    /// description goes **under the input** for the selected item only — which
    /// is the half that was actually being read anyway, and it means the
    /// completion list itself stays scannable.
    pub fn suggestion_detail(&self, cvars: &ClientCvars) -> Option<String> {
        let name = self.suggestions.get(self.suggestion)?;
        if let Some(def) = self.cvar_index.get(name) {
            let current = if def.is_client() {
                cvars
                    .get(&def.name)
                    .cloned()
                    .unwrap_or_else(|| def.default_value.clone())
            } else {
                self.server_view
                    .get(&def.name)
                    .cloned()
                    .unwrap_or_else(|| def.current_value.clone())
            };
            let scope = if def.is_client() { "client" } else { "server" };
            return Some(format!(
                "{} <{}> {} = {} (default {}) - {}",
                def.name,
                def.kind,
                scope,
                render(&current),
                render(&def.default_value),
                def.description
            ));
        }
        if let Some(cmd) = self.defs.commands.iter().find(|c| &c.name == name) {
            let signature = if cmd.signature.is_empty() {
                cmd.name.clone()
            } else {
                cmd.signature.clone()
            };
            return Some(format!("{signature} - {}", cmd.description));
        }
        if let Some(m) = self.defs.macros.iter().find(|m| &m.name == name) {
            return Some(format!("macro {} - {}", m.name, m.description));
        }
        None
    }

    /// Look one CVar up in the served registry.
    pub fn cvar(&self, name: &str) -> Option<&CVarDef> {
        self.cvar_index.get(name)
    }

    // -- history --------------------------------------------------------------

    pub fn history_prev(&mut self) {
        if self.history.is_empty() {
            return;
        }
        self.history_index = self.history_index.saturating_sub(1);
        self.set_input(self.history[self.history_index].clone());
    }

    pub fn history_next(&mut self) {
        if self.history.is_empty() {
            return;
        }
        if self.history_index + 1 >= self.history.len() {
            self.history_index = self.history.len();
            self.set_input(String::new());
        } else {
            self.history_index += 1;
            self.set_input(self.history[self.history_index].clone());
        }
    }

    fn set_input(&mut self, text: String) {
        self.cursor = text.len();
        self.input = text;
        self.refresh_suggestions();
    }

    // -- editing --------------------------------------------------------------

    pub fn insert(&mut self, text: &str) {
        // Only what the font can draw. A glyph the HUD has no shape for renders
        // as an invisible column, so accepting it would produce an input line
        // that does not match what was typed — and a command that fails for a
        // reason nothing on screen explains.
        let filtered: String = text.chars().filter(|c| crate::hud::has_glyph(*c)).collect();
        if filtered.is_empty() {
            return;
        }
        self.input.insert_str(self.cursor, &filtered);
        self.cursor += filtered.len();
        self.refresh_suggestions();
    }

    pub fn backspace(&mut self) {
        if self.cursor == 0 {
            return;
        }
        let prev = self.input[..self.cursor]
            .char_indices()
            .next_back()
            .map(|(i, _)| i)
            .unwrap_or(0);
        self.input.replace_range(prev..self.cursor, "");
        self.cursor = prev;
        self.refresh_suggestions();
    }

    pub fn move_cursor(&mut self, delta: i32) {
        if delta < 0 {
            self.cursor = self.input[..self.cursor]
                .char_indices()
                .next_back()
                .map(|(i, _)| i)
                .unwrap_or(0);
        } else if self.cursor < self.input.len() {
            let step = self.input[self.cursor..]
                .chars()
                .next()
                .map(|c| c.len_utf8())
                .unwrap_or(0);
            self.cursor += step;
        }
    }

    pub fn scroll_by(&mut self, delta: i32, page: usize) {
        let max = self.log.len().saturating_sub(1);
        if delta < 0 {
            self.scroll = self.scroll.saturating_sub(page);
        } else {
            self.scroll = (self.scroll + page).min(max);
        }
    }

    // -- completion -----------------------------------------------------------

    /// Everything the current first token could become.
    ///
    /// Completion is over the **served** names, so it cannot offer a command
    /// this node does not have — which is the whole reason the registry is
    /// fetched rather than declared.
    fn refresh_suggestions(&mut self) {
        self.suggestion = 0;
        self.suggestions.clear();
        let head = self.input.split_whitespace().next().unwrap_or("");
        // Only while typing the first token: completing an argument would need
        // to know the command's parameter list, and offering CVar names in an
        // argument slot is worse than offering nothing.
        if head.is_empty() || self.input[..self.cursor].contains(' ') {
            return;
        }
        let needle = head.to_ascii_lowercase();
        let mut out: Vec<String> = self
            .cvar_index
            .keys()
            .chain(self.defs.commands.iter().map(|c| &c.name))
            .chain(self.aliases.keys())
            .filter(|n| n.to_ascii_lowercase().starts_with(&needle))
            .cloned()
            .collect();
        out.sort();
        out.dedup();
        out.truncate(12);
        self.suggestions = out;
    }

    /// Accept the selected completion.
    pub fn complete(&mut self) {
        let Some(pick) = self.suggestions.get(self.suggestion).cloned() else {
            return;
        };
        let rest: Vec<&str> = self.input.split_whitespace().skip(1).collect();
        let line = if rest.is_empty() {
            format!("{pick} ")
        } else {
            format!("{pick} {}", rest.join(" "))
        };
        self.set_input(line);
    }

    pub fn cycle_suggestion(&mut self, delta: i32) {
        if self.suggestions.is_empty() {
            return;
        }
        let n = self.suggestions.len() as i32;
        self.suggestion = (((self.suggestion as i32 + delta) % n + n) % n) as usize;
    }

    // -- execution ------------------------------------------------------------

    /// Run whatever is on the input line.
    ///
    /// Returns what the caller must do with it. See `Dispatch` for why this
    /// returns an instruction instead of sending anything itself.
    pub fn submit(&mut self, cvars: &mut ClientCvars, online: bool) -> Dispatch {
        let line = self.input.trim().to_string();
        self.input.clear();
        self.cursor = 0;
        self.suggestions.clear();
        if line.is_empty() {
            return Dispatch::Handled;
        }
        if self.history.last().map(|h| h != &line).unwrap_or(true) {
            self.history.push(line.clone());
            while self.history.len() > MAX_HISTORY {
                self.history.remove(0);
            }
        }
        self.history_index = self.history.len();
        self.push(format!("] {line}"), Tone::Echo);
        self.execute(&line, cvars, online)
    }

    /// The one place a console line is interpreted.
    ///
    /// Separate from `submit` so a test can drive it without going through the
    /// input line, and so a key bound with `bind` can run a command that was
    /// never typed.
    pub fn execute(&mut self, line: &str, cvars: &mut ClientCvars, online: bool) -> Dispatch {
        let expanded = self.expand_alias(line);
        let tokens: Vec<String> = expanded.split_whitespace().map(|t| t.to_string()).collect();
        let Some(head) = tokens.first().cloned() else {
            return Dispatch::Handled;
        };
        let lower = head.to_ascii_lowercase();

        match lower.as_str() {
            "clear" => {
                self.log.clear();
                return Dispatch::Handled;
            }
            "divergence" | "divergences" => {
                self.report_divergence();
                return Dispatch::Handled;
            }
            // The browser's filter tabs. A command as well as a key, because a
            // key is only discoverable once somebody has told you it exists and
            // `help` lists commands.
            "filter" => {
                if let Some(name) = tokens.get(1) {
                    match Filter::parse(name) {
                        Some(f) => {
                            self.filter = f;
                            self.scroll = 0;
                        }
                        None => {
                            let names: Vec<&str> = Filter::ALL.iter().map(|f| f.label()).collect();
                            self.error(format!("no filter '{name}' - try {}", names.join(", ")));
                            return Dispatch::Handled;
                        }
                    }
                } else {
                    self.cycle_filter();
                }
                let hidden = self.hidden_count();
                self.note(format!(
                    "filter: {} ({hidden} lines hidden)",
                    self.filter.label()
                ));
                return Dispatch::Handled;
            }
            // The served macro list. `macro.run(...)` still goes to the node —
            // this only answers "what is there to run", which the browser
            // answers with a drawer and this answers with a list.
            "macros" => {
                if self.defs.macros.is_empty() {
                    self.error("no macros - the registry has not loaded, or the node serves none");
                } else {
                    for m in &self.defs.macros.clone() {
                        self.push(
                            format!("macro.run(\"{}\")  - {}", m.name, m.description),
                            Tone::Output,
                        );
                    }
                }
                return Dispatch::Handled;
            }
            // Printed here **and** still forwarded: the node owns the list of
            // CVars and commands, and this owns the handful that never leave the
            // client. A `help` that answered only from one side would leave
            // whichever half it skipped undiscoverable.
            "help" | "?" => {
                for line in CLIENT_HELP {
                    self.push(*line, Tone::Output);
                }
                // Falls through to the dispatch below.
            }
            // The browser copies to the clipboard; this writes a file. See
            // `Dispatch::SaveTranscript`.
            "copy" | "save" => {
                return Dispatch::SaveTranscript {
                    text: self.transcript(),
                };
            }
            "bind" if tokens.len() >= 3 => {
                let key = tokens[1].to_ascii_lowercase();
                let cmd = unquote(&tokens[2..].join(" "));
                self.binds.insert(key.clone(), cmd.clone());
                self.note(format!("bound '{key}' to \"{cmd}\""));
                return Dispatch::Handled;
            }
            "unbind" if tokens.len() >= 2 => {
                let key = tokens[1].to_ascii_lowercase();
                self.binds.remove(&key);
                self.note(format!("unbound '{key}'"));
                return Dispatch::Handled;
            }
            "alias" if tokens.len() >= 3 => {
                let name = tokens[1].to_ascii_lowercase();
                let cmd = unquote(&tokens[2..].join(" "));
                self.aliases.insert(name.clone(), cmd.clone());
                self.note(format!("alias '{name}' = \"{cmd}\""));
                return Dispatch::Handled;
            }
            _ => {}
        }

        // A client CVar, queried or assigned. Checked before dispatch so it
        // never reaches the wire: the server does not apply these and answering
        // from it would report the registry's default rather than what this
        // client is actually drawing with.
        if let Some(def) = self.cvar_index.get(&head).cloned() {
            if def.is_client() && !head.contains('(') {
                return self.client_cvar(&def, &tokens, cvars);
            }
        }

        if !online {
            self.error(format!(
                "'{head}' needs the node - this client is offline (train has no server)"
            ));
            return Dispatch::Handled;
        }

        self.next_req += 1;
        Dispatch::Send {
            command: expanded,
            req_id: self.next_req,
        }
    }

    fn client_cvar(
        &mut self,
        def: &CVarDef,
        tokens: &[String],
        cvars: &mut ClientCvars,
    ) -> Dispatch {
        // Bare name: report the live value, falling back to the served default —
        // which is what an unset override *means*, and the reason the fallback
        // is spelled out rather than hidden in the getter.
        if tokens.len() == 1 {
            let current = cvars
                .get(&def.name)
                .cloned()
                .unwrap_or_else(|| def.default_value.clone());
            self.push(
                format!(
                    "{} = {} (default {}) - {}",
                    def.name,
                    render(&current),
                    render(&def.default_value),
                    def.description
                ),
                Tone::Output,
            );
            return Dispatch::Handled;
        }

        // `name value` and `name = value` both, because both are muscle memory
        // from different games and neither is ambiguous.
        let raw = if tokens.len() >= 3 && tokens[1] == "=" {
            tokens[2..].join(" ")
        } else {
            tokens[1..].join(" ")
        };

        match def.coerce(&raw) {
            Ok(value) => {
                let shown = render(&value);
                let honored = ClientCvars::is_honored(&def.name);
                cvars.set(&def.name, value);
                if honored {
                    self.push(format!("{} = {shown}", def.name), Tone::Output);
                } else {
                    // The honesty rule. See the module docs: a console that
                    // reported this as a plain success would be lying.
                    self.push(
                        format!(
                            "{} = {shown}  [stored - no reader here yet; the browser pane has one]",
                            def.name
                        ),
                        Tone::Error,
                    );
                }
                Dispatch::Handled
            }
            Err(e) => {
                self.error(e);
                Dispatch::Handled
            }
        }
    }

    /// Print what this client is known to be ignoring.
    ///
    /// Two lists, because they fail differently: CVars the node serves and this
    /// build has no reader for are a *known* gap, while the `divergence` log is
    /// what the running server has actually sent that this build dropped.
    fn report_divergence(&mut self) {
        let unread = self.unread_cvars();
        if unread.is_empty() {
            self.note("every client cvar this node serves is read here");
        } else {
            self.note(format!(
                "client cvars with no reader here ({}):",
                unread.len()
            ));
            for name in unread {
                self.push(format!("  {name}"), Tone::Output);
            }
        }
        let seen = divergence::seen();
        if seen.is_empty() {
            self.note("nothing on the wire has been dropped this session");
        } else {
            self.note(format!(
                "dropped from the wire this session ({}):",
                seen.len()
            ));
            for item in seen {
                self.push(format!("  {item}"), Tone::Output);
            }
        }
    }

    fn expand_alias(&self, line: &str) -> String {
        let head = line.split_whitespace().next().unwrap_or("");
        match self.aliases.get(&head.to_ascii_lowercase()) {
            Some(target) => {
                let tail = line.trim_start();
                format!("{target}{}", &tail[head.len()..])
            }
            None => line.to_string(),
        }
    }

    /// The command bound to a key, if any. Lower-cased on both sides so `F5`
    /// and `f5` are the same key.
    pub fn bound(&self, key: &str) -> Option<String> {
        self.binds.get(&key.to_ascii_lowercase()).cloned()
    }
}

fn unquote(s: &str) -> String {
    s.trim()
        .trim_start_matches(['"', '\''])
        .trim_end_matches(['"', '\''])
        .to_string()
}

/// A JSON value as a console would say it — `1` not `1.0`, `1` not `true`, and a
/// bare string with no quotes around it.
/// The commands that never reach the node, as `help` lists them.
///
/// Spelled out rather than derived from the match below, because the match arms
/// carry guards and aliases that would make a generated list either wrong or
/// unreadable. The cost is that this can drift; the alternative was a help text
/// that listed `bind` three times.
const CLIENT_HELP: &[&str] = &[
    "-- client-side (never sent to the node) --",
    "  bind <key> <command>     run a command when a key is pressed",
    "  unbind <key>             forget a binding",
    "  alias <name> <command>   name a command",
    "  clear                    empty the scrollback",
    "  filter [all|net|draw|server|macro]   which lines to show (also ^F)",
    "  macros                   list the macros the node serves",
    "  copy                     write the scrollback to a file",
    "  divergence               what this client ignores that the browser does not",
    "  F1-F8                    the quick actions on the chip row",
];

/// Whether a JSON value reads as "on".
///
/// The registry types a boolean CVar as a bool and a 0/1 one as a number, and
/// both mean the same thing to a toggle. Anything else is not a claim of `false`
/// — it is a value this has no opinion about, and the callers that care draw
/// nothing rather than `OFF`.
fn truthy(value: &serde_json::Value) -> bool {
    match value {
        serde_json::Value::Bool(b) => *b,
        serde_json::Value::Number(n) => n.as_f64().unwrap_or(0.0) != 0.0,
        _ => false,
    }
}

fn render(v: &serde_json::Value) -> String {
    match v {
        serde_json::Value::Bool(b) => if *b { "1" } else { "0" }.to_string(),
        serde_json::Value::String(s) => s.clone(),
        serde_json::Value::Number(n) => {
            let f = n.as_f64().unwrap_or(0.0);
            if f.fract() == 0.0 && f.abs() < 1e15 {
                format!("{}", f as i64)
            } else {
                format!("{f}")
            }
        }
        serde_json::Value::Null => "-".to_string(),
        other => other.to_string(),
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// A registry shaped like the node's, small enough to reason about.
    ///
    /// Built by hand rather than fetched: the point of these tests is the
    /// *split* between what is applied here and what goes on the wire, and that
    /// split is decided by the `client` flag, not by which names happen to exist
    /// on some node.
    fn defs() -> Definitions {
        Definitions {
            cvars: vec![
                CVarDef {
                    name: "net.graph".into(),
                    kind: "number".into(),
                    default_value: json!(0),
                    min_value: Some(0.0),
                    max_value: Some(3.0),
                    flags: vec!["client".into()],
                    description: "network graph".into(),
                    ..Default::default()
                },
                CVarDef {
                    name: "net.prediction".into(),
                    kind: "boolean".into(),
                    default_value: json!(true),
                    flags: vec!["client".into()],
                    description: "client prediction".into(),
                    ..Default::default()
                },
                CVarDef {
                    name: "server.cheats".into(),
                    kind: "boolean".into(),
                    default_value: json!(false),
                    flags: vec!["server".into(), "cheat".into()],
                    ..Default::default()
                },
                // The two the quick-action row reads. Present here for the same
                // reason the rest are: the row asks the registry which side owns
                // a name, and a fixture missing them would exercise the
                // "unknown cvar" path instead of the one under test.
                CVarDef {
                    name: "server.timescale".into(),
                    kind: "number".into(),
                    default_value: json!(1.0),
                    current_value: json!(1.0),
                    flags: vec!["server".into(), "cheat".into()],
                    ..Default::default()
                },
                CVarDef {
                    name: "player.god".into(),
                    kind: "boolean".into(),
                    default_value: json!(false),
                    flags: vec!["server".into(), "cheat".into()],
                    ..Default::default()
                },
                CVarDef {
                    name: "draw.wireframe".into(),
                    kind: "boolean".into(),
                    default_value: json!(false),
                    flags: vec!["client".into()],
                    description: "wireframe".into(),
                    ..Default::default()
                },
                CVarDef {
                    name: "draw.hitboxes".into(),
                    kind: "boolean".into(),
                    default_value: json!(false),
                    flags: vec!["client".into()],
                    description: "draw hitboxes".into(),
                    ..Default::default()
                },
            ],
            commands: vec![ConCommandDef {
                name: "server.bots.add".into(),
                ..Default::default()
            }],
            macros: vec![],
        }
    }

    fn console() -> (Console, ClientCvars) {
        let mut c = Console::default();
        c.set_definitions(defs());
        (c, ClientCvars::default())
    }

    #[test]
    fn a_client_cvar_is_applied_here_and_never_sent() {
        let (mut c, mut cvars) = console();
        let d = c.execute("net.graph 2", &mut cvars, true);
        assert_eq!(
            d,
            Dispatch::Handled,
            "the server does not apply client cvars, so sending one asks a \
             question nobody can answer"
        );
        assert_eq!(cvars.number("net.graph"), Some(2.0));
    }

    #[test]
    fn a_server_command_goes_to_the_node_verbatim() {
        let (mut c, mut cvars) = console();
        match c.execute("server.bots.add(count=3)", &mut cvars, true) {
            Dispatch::Send { command, req_id } => {
                assert_eq!(command, "server.bots.add(count=3)");
                assert_eq!(req_id, 1, "ids start at one so zero can mean unset");
            }
            other => panic!("expected a send, got {other:?}"),
        }
    }

    #[test]
    fn a_server_command_offline_is_refused_rather_than_swallowed() {
        // Train has no socket. The failure this guards against is the console
        // accepting the line, sending nothing, and printing nothing — which is
        // indistinguishable from a command that ran and did nothing.
        let (mut c, mut cvars) = console();
        let d = c.execute("server.bots.add(count=3)", &mut cvars, false);
        assert_eq!(d, Dispatch::Handled);
        let last = c.lines().back().expect("something was said");
        assert_eq!(last.tone, Tone::Error);
        assert!(last.text.contains("offline"), "got {:?}", last.text);
    }

    #[test]
    fn setting_a_cvar_with_no_reader_here_says_so() {
        // The honesty rule. `net.prediction` is a real client CVar the browser
        // pane acts on and this client does not, and a console that reported
        // plain success would have told the player something false.
        let (mut c, mut cvars) = console();
        c.execute("net.prediction 0", &mut cvars, true);
        let last = c.lines().back().unwrap();
        assert_eq!(
            last.tone,
            Tone::Error,
            "a stored-but-unread value is not a success"
        );
        assert!(last.text.contains("no reader here"), "got {:?}", last.text);
        assert_eq!(
            cvars.boolean("net.prediction"),
            Some(false),
            "it is still stored: the moment a reader exists it must already work"
        );
    }

    #[test]
    fn a_bare_cvar_name_reports_the_default_until_it_is_set() {
        let (mut c, mut cvars) = console();
        c.execute("net.graph", &mut cvars, true);
        assert!(c.lines().back().unwrap().text.contains("net.graph = 0"));
        c.execute("net.graph 3", &mut cvars, true);
        c.execute("net.graph", &mut cvars, true);
        let text = c.lines().back().unwrap().text.clone();
        assert!(text.contains("net.graph = 3"), "got {text:?}");
        assert!(text.contains("default 0"), "the default is still reported");
    }

    #[test]
    fn a_number_is_clamped_to_the_served_range_and_a_boolean_is_refused() {
        let (mut c, mut cvars) = console();
        c.execute("net.graph 99", &mut cvars, true);
        assert_eq!(
            cvars.number("net.graph"),
            Some(3.0),
            "clamped, because 99 is a typo with an obvious intent"
        );
        c.execute("net.prediction purple", &mut cvars, true);
        assert_eq!(
            c.lines().back().unwrap().tone,
            Tone::Error,
            "purple has no obvious intent, so it is refused rather than guessed"
        );
    }

    #[test]
    fn an_equals_sign_is_accepted_like_a_space() {
        let (mut c, mut cvars) = console();
        c.execute("net.graph = 2", &mut cvars, true);
        assert_eq!(cvars.number("net.graph"), Some(2.0));
    }

    #[test]
    fn bind_alias_and_clear_never_leave_the_client() {
        let (mut c, mut cvars) = console();
        assert_eq!(
            c.execute("bind f5 \"net.graph 3\"", &mut cvars, true),
            Dispatch::Handled
        );
        assert_eq!(
            c.bound("F5").as_deref(),
            Some("net.graph 3"),
            "keys fold case"
        );
        assert_eq!(
            c.execute("alias g net.graph", &mut cvars, true),
            Dispatch::Handled
        );
        c.execute("g 1", &mut cvars, true);
        assert_eq!(cvars.number("net.graph"), Some(1.0), "the alias expanded");
        c.execute("clear", &mut cvars, true);
        assert!(c.lines().is_empty());
    }

    #[test]
    fn only_client_cvars_are_taken_from_a_response() {
        // A macro reports every CVar it touched. Writing `server.cheats` into
        // the local map would create a second copy of a value the server owns —
        // one that goes stale the moment anything else changes it.
        let (mut c, mut cvars) = console();
        let mut affected = serde_json::Map::new();
        affected.insert("net.graph".into(), json!(3));
        affected.insert("server.cheats".into(), json!(true));
        let res = crate::protocol::ConsoleResponse {
            ok: true,
            output: vec!["[macro] warmup ready".into()],
            affected_cvars: affected,
            ..Default::default()
        };
        c.on_response(&res, &mut cvars);
        assert_eq!(cvars.number("net.graph"), Some(3.0));
        assert!(
            cvars.get("server.cheats").is_none(),
            "a server cvar is the server's; a local copy of one goes stale"
        );
    }

    #[test]
    fn both_spellings_of_affected_cvars_are_read() {
        // `channel.py` writes camelCase and the REST route writes snake_case for
        // the same field. A client that read one would silently drop half the
        // CVar updates depending on which pipe the answer came down.
        let camel: crate::protocol::ConsoleResponse =
            serde_json::from_value(json!({"affectedCvars": {"net.graph": 2}})).unwrap();
        let snake: crate::protocol::ConsoleResponse =
            serde_json::from_value(json!({"affected_cvars": {"net.graph": 2}})).unwrap();
        assert_eq!(camel.affected_cvars.len(), 1);
        assert_eq!(snake.affected_cvars.len(), 1);
    }

    #[test]
    fn completion_offers_only_names_the_node_actually_serves() {
        let (mut c, _) = console();
        c.insert("net.");
        assert_eq!(c.suggestions, vec!["net.graph", "net.prediction"]);
        c.insert("g");
        assert_eq!(c.suggestions, vec!["net.graph"]);
        c.complete();
        assert_eq!(c.input, "net.graph ");
    }

    #[test]
    fn completion_stops_once_an_argument_is_being_typed() {
        // Offering CVar names in an argument slot is worse than offering
        // nothing: Tab would replace the argument with a command name.
        let (mut c, _) = console();
        c.insert("net.graph 2");
        assert!(c.suggestions.is_empty());
    }

    #[test]
    fn the_input_line_only_accepts_what_the_font_can_draw() {
        // A glyph with no shape paints an invisible column, so accepting one
        // would leave the input line and the text on screen disagreeing — in the
        // one surface whose entire job is saying exactly what happened.
        let (mut c, _) = console();
        c.insert("net\u{2318}.graph");
        assert_eq!(c.input, "net.graph");
    }

    #[test]
    fn history_walks_back_and_returns_to_an_empty_line() {
        let (mut c, mut cvars) = console();
        c.insert("net.graph 1");
        c.submit(&mut cvars, true);
        c.insert("net.graph 2");
        c.submit(&mut cvars, true);
        c.history_prev();
        assert_eq!(c.input, "net.graph 2");
        c.history_prev();
        assert_eq!(c.input, "net.graph 1");
        c.history_next();
        assert_eq!(c.input, "net.graph 2");
        c.history_next();
        assert_eq!(c.input, "", "past the newest is a fresh line, not a wrap");
    }

    #[test]
    fn the_caret_moves_by_characters_and_backspace_deletes_one() {
        let (mut c, _) = console();
        c.insert("abc");
        assert_eq!(c.cursor, 3);
        c.move_cursor(-1);
        c.backspace();
        assert_eq!(c.input, "ac", "deleted before the caret, not at the end");
        assert_eq!(c.cursor, 1);
    }

    #[test]
    fn the_registry_names_what_this_client_does_not_read() {
        // The list that keeps the "still lacks" table honest: it is derived from
        // the served registry every launch rather than written down anywhere.
        let (c, _) = console();
        assert_eq!(
            c.unread_cvars(),
            vec!["net.prediction", "draw.wireframe"],
            "net.graph is in HONORED; server.cheats is not a client cvar at all,              and draw.wireframe is one the browser reads and this client does not"
        );
    }

    #[test]
    fn every_honored_name_is_spelled_like_a_cvar_and_the_list_is_sorted() {
        // A typo in `HONORED` is invisible: the name simply never matches, the
        // CVar reports itself unread, and the reader that does exist is never
        // credited. Cheap insurance against that.
        let mut sorted = HONORED.to_vec();
        sorted.sort();
        assert_eq!(sorted, HONORED, "kept sorted so a duplicate is obvious");
        for name in HONORED {
            assert!(
                name.contains('.') && !name.contains(' '),
                "{name} is not a cvar name"
            );
        }
    }

    #[test]
    fn the_scrollback_is_capped_and_drops_the_oldest() {
        let (mut c, _) = console();
        for i in 0..(MAX_LOG + 50) {
            c.push(format!("line {i}"), Tone::Output);
        }
        assert_eq!(c.lines().len(), MAX_LOG);
        assert!(c.lines().front().unwrap().text.contains("line 50"));
    }

    #[test]
    fn multi_line_output_becomes_one_line_each() {
        // Server output arrives as paragraphs; a painter that had to wrap would
        // need to know the font.
        let (mut c, _) = console();
        // A delta, not a total: `set_definitions` has already said its piece
        // about the registry it loaded.
        let before = c.lines().len();
        c.push("a\nb\nc", Tone::Output);
        assert_eq!(c.lines().len() - before, 3);
    }
    // -- filters ---------------------------------------------------------------

    #[test]
    fn a_line_can_belong_to_more_than_one_filter() {
        // `server.bots.add` is about the server *and* about bots. Forcing a line
        // into one bucket would make it disappear from a tab it plainly belongs
        // in, which is why `channels` is a mask rather than a value.
        let mask = classify("server.bots.add(count=3)");
        assert!(mask & channel::SERVER != 0);
        let net = classify("net.graph = 2");
        assert!(net & channel::NET != 0);
        assert!(net & channel::DRAW == 0, "a net line is not a draw line");
    }

    #[test]
    fn a_filter_hides_lines_and_says_how_many() {
        let (mut c, _) = console();
        c.push("net.graph = 2", Tone::Output);
        c.push("draw.hitboxes = 1", Tone::Output);
        c.push("nothing in particular", Tone::Output);

        c.filter = Filter::Net;
        let visible: Vec<&str> = c.visible().iter().map(|l| l.text.as_str()).collect();
        assert_eq!(visible, vec!["net.graph = 2"]);
        // Everything the registry note printed at load counts too, which is the
        // point: the header's number is "lines you are not being shown", not
        // "lines from this test".
        assert_eq!(c.hidden_count(), c.lines().len() - 1);
    }

    #[test]
    fn changing_filter_resets_the_scroll() {
        // `scroll` counts lines back from the bottom of the *visible* set, so
        // keeping the offset across a filter change parks the view at an
        // unrelated place in the log with no way to tell that is what happened.
        let (mut c, _) = console();
        for i in 0..40 {
            c.push(format!("net.line {i}"), Tone::Output);
        }
        c.scroll_by(1, 8);
        assert!(c.scroll > 0);
        c.cycle_filter();
        assert_eq!(c.scroll, 0);
    }

    #[test]
    fn a_transcript_ignores_the_filter() {
        // A filter is a reading aid; a transcript is evidence. One that silently
        // dropped three quarters of a session because a tab was selected would
        // be the worst possible thing to hand somebody debugging.
        let (mut c, _) = console();
        c.push("net.graph = 2", Tone::Output);
        c.push("draw.hitboxes = 1", Tone::Output);
        c.filter = Filter::Net;
        let text = c.transcript();
        assert!(text.contains("net.graph = 2"));
        assert!(
            text.contains("draw.hitboxes = 1"),
            "the transcript must carry lines the current filter hides"
        );
    }

    #[test]
    fn copy_asks_the_caller_to_write_it() {
        // The console is a pure state machine over text and does no file IO of
        // its own — the same reason sending a command is a `Dispatch` rather
        // than a socket write in here.
        let (mut c, mut cvars) = console();
        c.push("hello", Tone::Output);
        match c.execute("copy", &mut cvars, true) {
            Dispatch::SaveTranscript { text } => assert!(text.contains("hello")),
            other => panic!("expected a transcript, got {other:?}"),
        }
    }

    // -- quick actions ---------------------------------------------------------

    #[test]
    fn a_toggle_reads_its_command_from_the_current_state() {
        // The failure a static table would give: a key that turns hitboxes on
        // and can never turn them off again.
        let (c, mut cvars) = console();
        let off = c.quick_command(0, &cvars).expect("a first action");
        assert_eq!(off, "draw.hitboxes 1");
        cvars.set("draw.hitboxes", json!(1));
        let on = c.quick_command(0, &cvars).expect("a first action");
        assert_eq!(on, "draw.hitboxes 0");
    }

    #[test]
    fn a_quick_action_this_client_ignores_is_marked_unhonored() {
        // `draw.wireframe` is a client cvar the browser reads and this client
        // does not. The chip is still drawn — hiding it would be a quieter lie
        // than drawing it wrong — but it is drawn as unhonored.
        let (c, cvars) = console();
        let actions = c.quick_actions(&cvars);
        let wireframe = actions
            .iter()
            .find(|a| a.label == "WIREFRAME")
            .expect("a wireframe chip");
        assert!(
            !wireframe.honored,
            "nothing here reads draw.wireframe, and the chip has to say so"
        );
        let hitboxes = actions
            .iter()
            .find(|a| a.label == "HITBOXES")
            .expect("a hitboxes chip");
        assert!(hitboxes.honored);
    }

    #[test]
    fn a_server_state_never_seen_is_absent_rather_than_off() {
        // The whole module's rule. Drawing `OFF` for "this client has never been
        // told" is a claim, and it is the claim a player would act on.
        let (c, cvars) = console();
        let god = c
            .quick_actions(&cvars)
            .into_iter()
            .find(|a| a.label == "GOD")
            .expect("a god chip");
        assert_eq!(god.state, None);
    }

    #[test]
    fn a_server_answer_updates_the_chip_it_owns() {
        let (mut c, mut cvars) = console();
        let mut affected = serde_json::Map::new();
        affected.insert("server.cheats".into(), json!(true));
        c.on_response(
            &crate::protocol::ConsoleResponse {
                affected_cvars: affected,
                ok: true,
                ..Default::default()
            },
            &mut cvars,
        );
        assert_eq!(c.server_bool("server.cheats"), Some(true));
        assert_eq!(
            cvars.get("server.cheats"),
            None,
            "a server cvar must not land in the client's own overrides"
        );
    }

    #[test]
    fn the_speed_action_cycles_rather_than_setting_one_value() {
        // The browser has three buttons; there is one key here, so it has to
        // walk the same three values.
        let (mut c, cvars) = console();
        let speed_index = 6;
        assert_eq!(
            c.quick_command(speed_index, &cvars).as_deref(),
            Some("server.timescale 2"),
            "from the default 1.0 the next step is 2x"
        );
        let mut affected = serde_json::Map::new();
        affected.insert("server.timescale".into(), json!(2.0));
        let mut sink = ClientCvars::default();
        c.on_response(
            &crate::protocol::ConsoleResponse {
                affected_cvars: affected,
                ok: true,
                ..Default::default()
            },
            &mut sink,
        );
        assert_eq!(
            c.quick_command(speed_index, &cvars).as_deref(),
            Some("server.timescale 0.35"),
            "and 2x wraps back to the slow one"
        );
    }

    // -- stamps ----------------------------------------------------------------

    #[test]
    fn a_stamp_is_minutes_and_seconds() {
        let line = LogLine {
            text: "x".into(),
            tone: Tone::Output,
            at: 125.7,
            channels: 0,
        };
        assert_eq!(line.stamp(), "02:05");
    }
}

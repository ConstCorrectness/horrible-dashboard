//! The front end: what the game opens into when nothing told it where to go.
//!
//! The client used to have exactly one screen. It fetched a map, joined a room
//! and opened a window with a match already running in it — which is right
//! when a dashboard launched it into a match the player had just picked there,
//! and wrong for someone who double-clicked an executable: no say in the map,
//! no way to look at what they carry, and a console window for the seconds the
//! fetch took.
//!
//! So a client started with no destination opens here instead:
//!
//! ```text
//!   BOOT ──► HOME ──► SEARCHING ──► LOADING ──► (the match) ──► HOME
//!            play / loadout / settings
//! ```
//!
//! ## A state machine and a painter, and nothing else
//!
//! This module owns no window, no socket and no thread. It holds what is on
//! screen, turns a click or a key into an [`Intent`], and draws itself into
//! the same overlay vertices the HUD uses. `shell.rs` owns the rest: it carries
//! intents out on worker threads and feeds the results back in.
//!
//! That split is what makes every screen here renderable by `hud_preview` and
//! testable without a GPU, and it is the same split `console.rs` and `menu.rs`
//! already make for the same reason.
//!
//! ## One layout, read twice
//!
//! [`Front::hits`] produces a rectangle for everything that can be clicked,
//! and both the painter and the mouse read it. Two copies of that arithmetic
//! is how a menu develops the bug where clicking a tile selects its neighbour.
//!
//! ## What "finding a match" is
//!
//! The server seats a join in a room on the asked map, opening one if there is
//! none — so there is no queue to wait in for a casual match, and this does
//! not pretend there is one. Searching is a real question with a real answer:
//! the open rooms are fetched, [`choose_room`] picks the busiest one with a
//! free seat that matches what was asked for, and if there is none a room is
//! opened and filled with bots. The screen says which of those happened.

use winit::keyboard::KeyCode;

use crate::api::WeaponSpec;
use crate::hud::{measure, Hit, OverlayVertex, Painter};
use crate::menu::LoadoutTable;
use crate::radar::Run;
use crate::text_kit::{Face, TextKit};
use crate::ui_theme as theme;

mod account;
pub use account::{
    wear_label, AccountView, CaseView, ItemView, ListingView, SignIn, WalletView, PROVIDERS,
};

/// The room size the server seats, for a room whose listing did not say.
const DEFAULT_SEATS: u32 = 10;
/// The most bots a fresh room is opened with.
pub const MAX_BOTS: u32 = 9;
/// The longest callsign the server keeps.
pub const CALLSIGN_LEN: usize = 16;
/// Radar runs past this are not drawn on the loading screen: the picture is a
/// backdrop, and a map detailed enough to exceed it would spend the overlay's
/// vertex budget on something nobody reads.
const PLAN_LIMIT: usize = 9000;

/// Which screen is up.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Screen {
    /// Fetching what every screen after this needs.
    Boot,
    Home,
    /// Asking the server which rooms are open.
    Searching,
    /// A map is being fetched and a room joined.
    Loading,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tab {
    Play,
    Loadout,
    /// What the account owns. For a guest, where to sign in.
    Inventory,
    /// The wallet, cases and the market.
    Store,
    /// The tools for whoever is building the game rather than playing it.
    Dev,
    Settings,
}

impl Tab {
    pub const ALL: [Tab; 6] = [
        Tab::Play,
        Tab::Loadout,
        Tab::Inventory,
        Tab::Store,
        Tab::Dev,
        Tab::Settings,
    ];

    fn label(self) -> &'static str {
        match self {
            Tab::Play => "PLAY",
            Tab::Loadout => "LOADOUT",
            Tab::Inventory => "INVENTORY",
            Tab::Store => "STORE",
            Tab::Dev => "DEV TOOLS",
            Tab::Settings => "SETTINGS",
        }
    }
}

/// What the DEV TOOLS tab can start. Each is something this executable
/// already did from a command line (`--drill`, `--bench`, `--mode=edit`); the
/// tab is how it is reached by somebody who double-clicked.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DevTool {
    /// An aim drill on the range, alone.
    Drill,
    /// Fly a map's benchmark path and report the frame times.
    Bench,
    /// The map designer, on a copy of a map.
    Editor,
    /// Every weapon in every finish, in the hands (`viewer.rs`).
    Viewer,
    /// The rooms this server is running and who is in them.
    Server,
}

impl DevTool {
    pub const ALL: [DevTool; 5] = [
        DevTool::Drill,
        DevTool::Bench,
        DevTool::Editor,
        DevTool::Viewer,
        DevTool::Server,
    ];

    fn title(self) -> &'static str {
        match self {
            DevTool::Drill => "AIM DRILL",
            DevTool::Bench => "BENCHMARK",
            DevTool::Editor => "MAP EDITOR",
            DevTool::Viewer => "MODEL VIEWER",
            DevTool::Server => "SERVER",
        }
    }

    fn about(self) -> &'static str {
        match self {
            DevTool::Drill => "timed targets on the range",
            DevTool::Bench => "fly the map, report frame times",
            DevTool::Editor => "needs a server that keeps drafts",
            DevTool::Viewer => "every weapon, finish and wear",
            DevTool::Server => "rooms running, and who is in",
        }
    }

    fn go(self) -> &'static str {
        match self {
            DevTool::Drill => "START DRILL",
            DevTool::Bench => "RUN BENCHMARK",
            DevTool::Editor => "OPEN EDITOR",
            DevTool::Viewer => "OPEN VIEWER",
            DevTool::Server => "JOIN ROOM",
        }
    }
}

/// The overlays DEV TOOLS can switch on for the next match, in row order.
/// Each is a console CVar this client reads (`console::HONORED`); the row is
/// the same switch without the typing.
pub const DEV_OVERLAYS: [&str; 3] = ["NET GRAPH", "HITBOXES", "OWN TRACERS"];

/// One member of a lobby.
#[derive(Debug, Clone, Default, PartialEq, serde::Deserialize)]
pub struct LobbyMember {
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub leader: bool,
    #[serde(default)]
    pub you: bool,
}

/// The room a lobby's search found.
#[derive(Debug, Clone, Default, PartialEq, serde::Deserialize)]
pub struct LobbyMatch {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub map: String,
    #[serde(default)]
    pub mode: String,
    #[serde(default)]
    pub opened: bool,
}

/// A lobby as this member sees it (`hassault_lobby.Lobby.payload`).
#[derive(Debug, Clone, Default, PartialEq, serde::Deserialize)]
pub struct LobbyView {
    #[serde(default)]
    pub code: String,
    #[serde(default)]
    pub members: Vec<LobbyMember>,
    /// Whether this member is the leader: the one whose button searches.
    #[serde(default)]
    pub leader: bool,
    /// Counts the lobby's searches. A member acts on each one once.
    #[serde(default)]
    pub serial: u64,
    #[serde(rename = "match", default)]
    pub found: Option<LobbyMatch>,
}

impl LobbyView {
    fn leader_name(&self) -> &str {
        self.members
            .iter()
            .find(|m| m.leader)
            .map(|m| m.name.as_str())
            .unwrap_or("THE LEADER")
    }
}

/// A lobby code is this long (`hassault_lobby.CODE_LENGTH`).
pub const LOBBY_CODE_LEN: usize = 5;

/// One picture for the image pass: a map's art, where, and how bright.
#[derive(Debug, Clone, PartialEq)]
pub struct ArtQuad {
    pub map: String,
    pub rect: Hit,
    pub tint: [f32; 4],
}

/// One seat in a room, as the scoreboard would show it.
#[derive(Debug, Clone, Default, PartialEq, serde::Deserialize)]
pub struct RosterRow {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub name: String,
    #[serde(default)]
    pub kills: i32,
    #[serde(default)]
    pub deaths: i32,
    #[serde(default)]
    pub bot: bool,
}

/// One room looked into (`/api/hassault/rooms/{id}`): its roster and host.
#[derive(Debug, Clone, Default, PartialEq, serde::Deserialize)]
pub struct RoomDetail {
    #[serde(default)]
    pub id: String,
    /// The player id of the room's host: its longest-seated human.
    #[serde(default)]
    pub host: String,
    #[serde(default)]
    pub roster: Vec<RosterRow>,
}

/// One aim drill, as `/api/hassault/drills` lists it.
#[derive(Debug, Clone, Default, PartialEq, serde::Deserialize)]
pub struct DrillEntry {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub name: String,
    /// The map it is built on; `None` runs on any.
    #[serde(default)]
    pub map: Option<String>,
}

/// The three ways into a map.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum PlayKind {
    /// Join the busiest matching room, or open one.
    Quick,
    /// Pick a room off the list.
    Browse,
    /// One player on a map, with no server in it.
    Practice,
}

impl PlayKind {
    pub const ALL: [PlayKind; 3] = [PlayKind::Quick, PlayKind::Browse, PlayKind::Practice];

    fn title(self) -> &'static str {
        match self {
            PlayKind::Quick => "QUICK PLAY",
            PlayKind::Browse => "BROWSE ROOMS",
            PlayKind::Practice => "PRACTICE",
        }
    }

    fn about(self) -> &'static str {
        match self {
            PlayKind::Quick => "busiest open room, or a new one",
            PlayKind::Browse => "pick a room off the list",
            PlayKind::Practice => "alone on a map, no server",
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct MapEntry {
    pub name: String,
    /// The served size of the map file, in bytes.
    pub size: u64,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct ModeEntry {
    pub id: String,
    pub name: String,
}

/// One open room, as `/api/hassault/rooms` lists it.
#[derive(Debug, Clone, Default, PartialEq, serde::Deserialize)]
pub struct RoomEntry {
    #[serde(default)]
    pub id: String,
    #[serde(default)]
    pub map: String,
    #[serde(rename = "playerCount", default)]
    pub players: u32,
    #[serde(rename = "maxPlayers", default)]
    pub seats: u32,
    #[serde(default)]
    pub mode: String,
}

impl RoomEntry {
    fn seats(&self) -> u32 {
        if self.seats == 0 {
            DEFAULT_SEATS
        } else {
            self.seats
        }
    }

    fn open(&self) -> bool {
        self.players < self.seats()
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StepState {
    Waiting,
    Running,
    Done,
    /// Did not work, and the client carries on without it.
    Skipped,
    Failed,
}

/// One line of a boot or loading screen: a real phase, with what it produced.
#[derive(Debug, Clone, PartialEq)]
pub struct Step {
    pub label: String,
    pub note: String,
    pub state: StepState,
}

impl Step {
    pub fn waiting(label: &str) -> Step {
        Step {
            label: label.to_string(),
            note: String::new(),
            state: StepState::Waiting,
        }
    }
}

/// What a search came back with.
#[derive(Debug, Clone, PartialEq)]
pub enum Found {
    /// A room with a free seat that matches what was asked for.
    Room(RoomEntry),
    /// Nothing open matched: a room is opened on this map.
    Open { map: String },
}

/// Something the player asked for that this module cannot do itself.
#[derive(Debug, Clone, PartialEq)]
pub enum Intent {
    /// Ask the server for open rooms and join the best, or open one.
    /// `maps` is every tile toggled on; empty is any map.
    FindMatch {
        maps: Vec<String>,
        mode: Option<String>,
        bots: u32,
    },
    /// Join this room, on this map.
    Join {
        room: String,
        map: String,
        mode: String,
    },
    /// Open `map` with no server.
    Practice {
        map: String,
    },
    /// Start a developer tool on `map`. `drill` is set for [`DevTool::Drill`].
    Tool {
        tool: DevTool,
        map: String,
        drill: Option<String>,
    },
    CancelSearch,
    RefreshRooms,
    /// Ask who is in this room, for the server view.
    Roster(String),
    /// Begin signing in with `PROVIDERS[n]`.
    SignIn(usize),
    SignInCancel,
    SignOut,
    /// Fetch the inventory, the wallet and the market again.
    AccountRefresh,
    /// Equip the skin with this instance id.
    Equip(String),
    /// Take the skin off this weapon.
    Unequip(String),
    BuyKey,
    OpenCase(String),
    BuyListing(String),
    /// Open a lobby and share its code.
    LobbyCreate,
    /// Join the lobby with this code.
    LobbyJoin(String),
    LobbyLeave,
    /// The lobby's leader searching for everyone in it.
    LobbyPlay {
        maps: Vec<String>,
        mode: Option<String>,
    },
    /// Save one settings key (`hassault.loadout`, `hassault.agents`).
    Save(&'static str, String),
    SaveCallsign(String),
    OpenSettings,
    /// Fetch the boot tables again, after they failed.
    Retry,
    Quit,
}

/// Everything on screen that can be clicked.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Target {
    Tab(Tab),
    Kind(PlayKind),
    /// `None` is ANY MAP.
    Map(Option<usize>),
    /// Step the game mode: -1 or +1.
    Mode(i32),
    Bots(i32),
    Room(usize),
    Refresh,
    Go,
    Cancel,
    Quit,
    Callsign,
    Side(usize),
    /// Select a loadout slot: 0 primary, 1 secondary, 2 operator.
    Slot(usize),
    /// Step the selected slot's choice.
    Step(usize, i32),
    Retry,
    Dismiss,
    LobbyCreate,
    /// Open the field a lobby code is typed into.
    LobbyJoin,
    LobbyLeave,
    Tool(DevTool),
    /// Step the drill: -1 or +1.
    Drill(i32),
    /// Toggle one of [`DEV_OVERLAYS`].
    Overlay(usize),
    /// Sign in with `PROVIDERS[n]`.
    Provider(usize),
    SignInCancel,
    SignOut,
    AccountRefresh,
    /// An owned skin: click to equip or take off.
    Item(usize),
    BuyKey,
    /// An owned case: click to open.
    Case(usize),
    /// A market listing: click to select.
    Listing(usize),
}

/// The screen while rooms are being asked for.
#[derive(Debug, Clone, Default)]
pub struct Search {
    /// What was asked for, as one line.
    pub asked: String,
    pub status: String,
    pub found: Option<Found>,
    started: f32,
}

/// The loading screen's subject.
#[derive(Debug, Clone, Default)]
pub struct Loading {
    pub map: String,
    /// The game being joined, as a name; empty until the server says.
    pub mode: String,
    /// `PRACTICE`, a room id, or `NEW ROOM`.
    pub room: String,
    /// The map's floor plan, once the geometry is built: the backdrop.
    pub plan: Vec<Run>,
    pub plan_size: f32,
}

pub struct Front {
    pub screen: Screen,
    pub tab: Tab,
    pub kind: PlayKind,
    /// The server, as a host name for the status line.
    pub server: String,
    pub callsign: String,
    /// The callsign being typed, while it is.
    pub editing: Option<String>,
    pub maps: Vec<MapEntry>,
    pub modes: Vec<ModeEntry>,
    pub rooms: Vec<RoomEntry>,
    /// Why the room list is empty, when it is not simply empty.
    pub rooms_error: Option<String>,
    /// The selected map; `None` is ANY MAP.
    pub map: Option<usize>,
    /// Quick play's maps: every tile toggled on, in the order they were. A
    /// search takes any of them; none is any map at all.
    pub picks: Vec<usize>,
    /// The lobby this player is in, as the server last described it.
    pub lobby: Option<LobbyView>,
    /// A lobby code being typed, while it is.
    pub entering_code: Option<String>,
    /// The maps there is a picture of, once the pictures have loaded. Empty
    /// until then, and the menu is drawn as flat cards on a flat slab.
    pub art: Vec<String>,
    /// 0 is ANY GAME; `n` is `modes[n - 1]`.
    pub mode: usize,
    pub bots: u32,
    /// The room list's cursor.
    pub room: usize,
    pub weapons: Vec<WeaponSpec>,
    pub loadout: LoadoutTable,
    /// The DEV TOOLS tab: the tool, the drills on offer and the one chosen,
    /// the maps that have a benchmark path, and the overlays switched on.
    pub tool: DevTool,
    pub drills: Vec<DrillEntry>,
    pub drill: usize,
    pub bench_maps: Vec<String>,
    pub overlays: [bool; DEV_OVERLAYS.len()],
    /// Who is signed in; `None` is a guest. See `front/account.rs`.
    pub account: Option<AccountView>,
    pub signin: SignIn,
    /// Which of `PROVIDERS` this server can sign in with.
    pub providers: [bool; PROVIDERS.len()],
    pub items: Vec<ItemView>,
    pub wallet: Option<WalletView>,
    pub listings: Vec<ListingView>,
    /// The market's cursor.
    pub listing: usize,
    /// The server view's selected room, looked into. Stale the moment the
    /// selection changes, so it is only shown while its id still matches.
    pub roster: Option<RoomDetail>,
    pub side: usize,
    pub slot: usize,
    pub steps: Vec<Step>,
    /// Set when boot could not get what the menu needs.
    pub boot_failed: Option<String>,
    pub search: Search,
    pub loading: Loading,
    /// A line across the top of HOME: what went wrong, or what just happened.
    pub notice: Option<(String, bool)>,
    pub pointer: (f32, f32),
    /// Seconds since the front end came up. Drives every animation, and is a
    /// field rather than a clock read so a test or a preview can set it.
    pub clock: f32,
    /// When the current screen or tab was entered.
    since: f32,
    /// When the loadout detail last changed subject.
    detail_since: f32,
}

/// The room to join for a search, if any is worth joining.
///
/// The busiest room with a free seat, among those on the asked map and in the
/// asked game. Rated rooms are never offered: a duel is paired by the server,
/// not joined, and it needs an account.
pub fn choose_room<'a>(
    rooms: &'a [RoomEntry],
    map: Option<&str>,
    mode: Option<&str>,
) -> Option<&'a RoomEntry> {
    rooms
        .iter()
        // Somebody has to be in it. A room lingers for a minute after its
        // last player leaves, and joining that is being seated alone in a room
        // this client did not open — so no bots are asked for either.
        .filter(|r| r.open() && r.players > 0 && r.mode != "duel")
        .filter(|r| map.is_none_or(|m| r.map == m))
        .filter(|r| mode.is_none_or(|m| r.mode == m))
        // Ties go to the first listed, so the choice does not flicker between
        // two equally full rooms from one refresh to the next.
        .rev()
        .max_by_key(|r| r.players)
}

/// `hd_dust2` as a title: `DUST2`.
pub fn map_title(name: &str) -> String {
    name.strip_prefix("hd_")
        .unwrap_or(name)
        .replace('_', " ")
        .to_uppercase()
}

fn smooth(t: f32) -> f32 {
    let t = t.clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

fn bytes_label(bytes: u64) -> String {
    if bytes >= 1024 * 1024 {
        format!("{:.1} MB", bytes as f64 / 1_048_576.0)
    } else {
        format!("{} KB", bytes.div_ceil(1024))
    }
}

const SIDE_LABELS: [&str; 2] = ["T SIDE", "CT SIDE"];
const SLOT_LABELS: [&str; 3] = ["PRIMARY", "SECONDARY", "OPERATOR"];

/// What the loading screen says under the map name. Each is true of this build.
const TIPS: &[&str] = &[
    "THE KEY ABOVE TAB OPENS THE CONSOLE - TYPE FIND AND A WORD TO SEARCH EVERY COMMAND",
    "ESC IN A MATCH OPENS SETTINGS WITHOUT LEAVING IT",
    "YOUR LOADOUT IS PER SIDE - SET BOTH BEFORE A MATCH THAT SWAPS",
    "SENSITIVITY IS DIVIDED BY THE SCOPE ZOOM, SO A FLICK FEELS THE SAME SCOPED",
    "TYPE DISCONNECT IN THE CONSOLE TO LEAVE A MATCH AND COME BACK HERE",
    "THE ROOM'S HOST CAN ADD BOTS FROM THE CONSOLE: BOT_ADD 3",
];

impl Front {
    pub fn new(server: &str, callsign: &str) -> Front {
        Front {
            screen: Screen::Boot,
            tab: Tab::Play,
            kind: PlayKind::Quick,
            server: server.to_string(),
            callsign: callsign.to_string(),
            editing: None,
            maps: Vec::new(),
            modes: Vec::new(),
            rooms: Vec::new(),
            rooms_error: None,
            map: None,
            picks: Vec::new(),
            lobby: None,
            entering_code: None,
            art: Vec::new(),
            mode: 0,
            bots: 5,
            room: 0,
            weapons: Vec::new(),
            loadout: LoadoutTable::default(),
            tool: DevTool::Drill,
            drills: Vec::new(),
            drill: 0,
            bench_maps: Vec::new(),
            overlays: [false; DEV_OVERLAYS.len()],
            account: None,
            signin: SignIn::Idle,
            providers: [false; PROVIDERS.len()],
            items: Vec::new(),
            wallet: None,
            listings: Vec::new(),
            listing: 0,
            roster: None,
            side: 0,
            slot: 0,
            steps: Vec::new(),
            boot_failed: None,
            search: Search::default(),
            loading: Loading::default(),
            notice: None,
            pointer: (-1.0, -1.0),
            clock: 0.0,
            since: 0.0,
            detail_since: 0.0,
        }
    }

    pub fn tick(&mut self, dt: f32) {
        self.clock += dt.max(0.0);
    }

    /// Change screen, restarting the entrance.
    pub fn show(&mut self, screen: Screen) {
        if self.screen != screen {
            self.screen = screen;
            self.since = self.clock;
        }
        self.editing = None;
    }

    pub fn set_tab(&mut self, tab: Tab) {
        if self.tab != tab {
            self.tab = tab;
            self.since = self.clock;
            self.detail_since = self.clock;
        }
    }

    /// Back to the menu with something to say about why.
    pub fn home_with(&mut self, message: impl Into<String>, error: bool) {
        self.notice = Some((message.into(), error));
        self.show(Screen::Home);
    }

    /// A search has begun for what is currently selected.
    pub fn begin_search(&mut self) {
        self.search = Search {
            asked: self.selection_label(),
            status: format!("ASKING {} FOR OPEN ROOMS", self.server.to_uppercase()),
            found: None,
            started: self.clock,
        };
        self.show(Screen::Searching);
    }

    /// The loading screen, for a map and with the phases about to run.
    pub fn begin_loading(&mut self, map: &str, mode: &str, room: &str, steps: &[&str]) {
        self.loading = Loading {
            map: map.to_string(),
            mode: mode.to_string(),
            room: room.to_string(),
            plan: Vec::new(),
            plan_size: 0.0,
        };
        self.steps = steps.iter().map(|s| Step::waiting(s)).collect();
        self.show(Screen::Loading);
    }

    /// Move a phase on. An index past the list is ignored: a worker reporting
    /// a phase this screen does not list is not a reason to panic mid-load.
    pub fn step(&mut self, index: usize, state: StepState, note: impl Into<String>) {
        if let Some(step) = self.steps.get_mut(index) {
            step.state = state;
            let note = note.into();
            if !note.is_empty() {
                step.note = note;
            }
        }
    }

    /// How much of the phase list is behind us, 0 to 1.
    pub fn progress(&self) -> f32 {
        if self.steps.is_empty() {
            return 0.0;
        }
        let done: f32 = self
            .steps
            .iter()
            .map(|s| match s.state {
                StepState::Done | StepState::Skipped => 1.0,
                // Half, so the bar moves when a phase starts and not only when
                // it ends: the longest phase would otherwise look like a hang.
                StepState::Running => 0.5,
                _ => 0.0,
            })
            .sum();
        done / self.steps.len() as f32
    }

    fn mode_id(&self) -> Option<&str> {
        (self.mode > 0)
            .then(|| self.modes.get(self.mode - 1))
            .flatten()
            .map(|m| m.id.as_str())
    }

    fn mode_label(&self) -> String {
        match self.mode.checked_sub(1).and_then(|i| self.modes.get(i)) {
            Some(mode) => mode.name.to_uppercase(),
            None => "ANY GAME".to_string(),
        }
    }

    /// A mode id as its served name.
    pub fn mode_name(&self, id: &str) -> String {
        self.modes
            .iter()
            .find(|m| m.id == id)
            .map(|m| m.name.to_uppercase())
            .unwrap_or_else(|| id.to_uppercase())
    }

    fn map_name(&self) -> Option<&str> {
        self.map
            .and_then(|i| self.maps.get(i))
            .map(|m| m.name.as_str())
    }

    /// Whether map tiles toggle: quick play's, where a search takes several.
    fn picking(&self) -> bool {
        self.tab != Tab::Dev && self.kind == PlayKind::Quick
    }

    /// The maps toggled on for quick play, by name, in the order picked.
    pub fn picked_maps(&self) -> Vec<String> {
        self.picks
            .iter()
            .filter_map(|i| self.maps.get(*i))
            .map(|m| m.name.clone())
            .collect()
    }

    /// Whether a map tile is drawn as chosen.
    fn map_active(&self, map: Option<usize>) -> bool {
        if !self.picking() {
            return map == self.map;
        }
        match map {
            None => self.picks.is_empty(),
            Some(i) => self.picks.contains(&i),
        }
    }

    /// The map whose picture is behind the menu, if there is one of it.
    fn backdrop_map(&self) -> Option<&str> {
        let has = |name: &&str| self.art.iter().any(|a| a == name);
        if self.screen == Screen::Loading {
            return Some(self.loading.map.as_str()).filter(has);
        }
        let cursor = self
            .maps
            .get(self.map.unwrap_or(0))
            .map(|m| m.name.as_str());
        let picked = self
            .picks
            .last()
            .and_then(|i| self.maps.get(*i))
            .map(|m| m.name.as_str());
        let busiest = self
            .rooms
            .iter()
            .max_by_key(|r| r.players)
            .map(|r| r.map.as_str());
        let chosen = if self.picking() {
            picked.or(busiest)
        } else {
            cursor
        };
        chosen
            .filter(has)
            .or_else(|| self.maps.iter().map(|m| m.name.as_str()).find(|n| has(n)))
            .or_else(|| self.art.first().map(String::as_str))
    }

    /// The pictures to draw under this frame: the backdrop, then a tile each.
    pub fn images(&self, kit: Option<&TextKit>, width: f32, height: f32) -> Vec<ArtQuad> {
        let mut out = Vec::new();
        if self.art.is_empty() || self.screen == Screen::Boot {
            return out;
        }
        if let Some(map) = self.backdrop_map() {
            // Dark, so everything drawn over it still reads; a little
            // brighter while loading, where it is the subject.
            // Linear light, on an sRGB surface: 0.065 reads as about a third
            // of the picture's brightness, not a fifteenth.
            let lit = if self.screen == Screen::Loading {
                0.11
            } else {
                0.065
            };
            out.push(ArtQuad {
                map: map.to_string(),
                rect: Hit {
                    x: 0.0,
                    y: 0.0,
                    w: width,
                    h: height,
                },
                tint: [lit, lit, lit * 1.08, 1.0],
            });
        }
        if self.screen != Screen::Home {
            return out;
        }
        let mut tile = 0;
        for (target, rect) in self.hits(kit, width, height) {
            let Target::Map(Some(i)) = target else {
                continue;
            };
            let a = self.arrive(tile + 3);
            tile += 1;
            let Some(entry) = self.maps.get(i) else {
                continue;
            };
            let active = if self.tab == Tab::Dev {
                i == self.map.unwrap_or(0)
            } else {
                self.map_active(Some(i))
            };
            let lit = if active {
                1.0
            } else if self.hovered(&rect) {
                0.6
            } else {
                0.24
            };
            out.push(ArtQuad {
                map: entry.name.clone(),
                rect,
                tint: [lit, lit, lit, a.clamp(0.0, 1.0)],
            });
        }
        out
    }

    /// The current PLAY selection, as the line under the button.
    pub fn selection_label(&self) -> String {
        let map = self
            .map_name()
            .map(map_title)
            .unwrap_or_else(|| "ANY MAP".into());
        let picked = match self.picks.as_slice() {
            [] => "ANY MAP".to_string(),
            [one] => self
                .maps
                .get(*one)
                .map(|m| map_title(&m.name))
                .unwrap_or_default(),
            many => format!("{} MAPS", many.len()),
        };
        match self.kind {
            PlayKind::Quick => match &self.lobby {
                Some(lobby) => format!(
                    "LOBBY OF {}  /  {picked}  /  {}",
                    lobby.members.len(),
                    self.mode_label()
                ),
                None => format!("{picked}  /  {}", self.mode_label()),
            },
            PlayKind::Practice => format!("{map}  /  OFFLINE"),
            PlayKind::Browse => match self.rooms.get(self.room) {
                Some(room) => format!(
                    "{}  /  {}  /  {} OF {}",
                    map_title(&room.map),
                    self.mode_name(&room.mode),
                    room.players,
                    room.seats()
                ),
                None => "NO ROOM SELECTED".into(),
            },
        }
    }

    /// The map a new room is opened on when the player asked for any.
    pub fn any_map(&self, seed: u64) -> Option<String> {
        if self.maps.is_empty() {
            return None;
        }
        Some(
            self.maps[(seed % self.maps.len() as u64) as usize]
                .name
                .clone(),
        )
    }

    /// How many rooms and players the list holds, for the status line.
    fn population(&self) -> (usize, u32) {
        (self.rooms.len(), self.rooms.iter().map(|r| r.players).sum())
    }

    /// How far item `i` of the current screen has arrived, 0 to 1.
    ///
    /// A stagger, capped: the tenth tile starts no later than the sixth, so a
    /// long list does not take seconds to finish arriving.
    fn arrive(&self, i: usize) -> f32 {
        let delay = (i as f32 * 0.03).min(0.18);
        smooth((self.clock - self.since - delay) / 0.2)
    }

    // -- input ----------------------------------------------------------------

    /// A left click at the pointer.
    pub fn click(&mut self, kit: Option<&TextKit>, width: f32, height: f32) -> Option<Intent> {
        let (x, y) = self.pointer;
        let target = self
            .hits(kit, width, height)
            .into_iter()
            .find(|(_, r)| r.contains(x, y))
            .map(|(t, _)| t);
        match target {
            Some(target) => self.activate(target),
            None => {
                // A click on nothing ends an edit, like clicking off a field.
                self.editing = None;
                self.entering_code = None;
                None
            }
        }
    }

    fn activate(&mut self, target: Target) -> Option<Intent> {
        if target != Target::Callsign {
            self.editing = None;
        }
        if target != Target::LobbyJoin {
            self.entering_code = None;
        }
        match target {
            Target::Tab(Tab::Settings) => return Some(Intent::OpenSettings),
            Target::Tab(tab) => {
                self.set_tab(tab);
                // An account tab shows what the server holds: ask as it opens.
                if matches!(tab, Tab::Inventory | Tab::Store) && self.account.is_some() {
                    return Some(Intent::AccountRefresh);
                }
            }
            Target::Provider(_)
            | Target::SignInCancel
            | Target::SignOut
            | Target::AccountRefresh
            | Target::Item(_)
            | Target::BuyKey
            | Target::Case(_)
            | Target::Listing(_) => return self.account_activate(target),
            Target::Kind(kind) => {
                if self.kind != kind {
                    self.kind = kind;
                    self.since = self.clock;
                    // Practice needs one map, not any of them.
                    if kind == PlayKind::Practice && self.map.is_none() && !self.maps.is_empty() {
                        self.map = Some(0);
                    }
                    if kind == PlayKind::Browse {
                        return Some(Intent::RefreshRooms);
                    }
                }
            }
            Target::Map(map) => {
                self.map = map;
                if self.picking() {
                    // Quick play's tiles toggle: any number may be on.
                    match map {
                        None => self.picks.clear(),
                        Some(i) => match self.picks.iter().position(|p| *p == i) {
                            Some(at) => {
                                self.picks.remove(at);
                            }
                            None => self.picks.push(i),
                        },
                    }
                }
            }
            Target::LobbyCreate => return Some(Intent::LobbyCreate),
            Target::LobbyJoin => self.entering_code = Some(String::new()),
            Target::LobbyLeave => return Some(Intent::LobbyLeave),
            Target::Mode(step) => {
                let n = self.modes.len() as i32 + 1;
                self.mode = (self.mode as i32 + step).rem_euclid(n) as usize;
            }
            Target::Bots(step) => {
                self.bots = (self.bots as i32 + step).clamp(0, MAX_BOTS as i32) as u32;
            }
            Target::Room(i) if self.tab == Tab::Dev => {
                // The server view looks into a room; only its button joins.
                // The first row is selected before anything is clicked, so
                // "click the selected room to join" would join on one click.
                self.room = i;
                return self.rooms.get(i).map(|r| Intent::Roster(r.id.clone()));
            }
            Target::Room(i) => {
                // A second click on the selected room joins it.
                if self.room == i && self.rooms.get(i).is_some() {
                    return self.go();
                }
                self.room = i;
            }
            Target::Refresh => return Some(Intent::RefreshRooms),
            Target::Go => return self.go(),
            Target::Cancel => return Some(Intent::CancelSearch),
            Target::Quit => return Some(Intent::Quit),
            Target::Callsign => self.editing = Some(self.callsign.clone()),
            Target::Side(side) => {
                if self.side != side {
                    self.side = side;
                    self.detail_since = self.clock;
                }
            }
            Target::Slot(slot) => {
                if self.slot != slot {
                    self.slot = slot;
                    self.detail_since = self.clock;
                }
            }
            Target::Step(slot, step) => {
                self.slot = slot;
                self.detail_since = self.clock;
                return self.step_slot(step);
            }
            Target::Retry => return Some(Intent::Retry),
            Target::Dismiss => self.notice = None,
            Target::Tool(tool) => {
                if self.tool != tool {
                    self.tool = tool;
                    self.since = self.clock;
                    if tool == DevTool::Server {
                        // The list first; the roster follows it.
                        return Some(Intent::RefreshRooms);
                    }
                }
            }
            Target::Drill(step) => {
                let n = self.drills.len().max(1) as i32;
                self.drill = (self.drill as i32 + step).rem_euclid(n) as usize;
            }
            Target::Overlay(i) => {
                if let Some(on) = self.overlays.get_mut(i) {
                    *on = !*on;
                }
            }
        }
        None
    }

    /// The map a developer tool runs on: the selected one, or the first.
    /// These tools need one map, where quick play can take any.
    fn dev_map(&self) -> Option<&str> {
        self.maps
            .get(self.map.unwrap_or(0))
            .or(self.maps.first())
            .map(|m| m.name.as_str())
    }

    fn dev_drill(&self) -> Option<&DrillEntry> {
        self.drills.get(self.drill)
    }

    /// Whether the selected tool can start, and if not, why.
    fn dev_ready(&self) -> Result<(), String> {
        if self.tool == DevTool::Server {
            return match self.rooms.get(self.room) {
                Some(_) => Ok(()),
                None => Err("NO ROOM IS RUNNING ON THIS SERVER".into()),
            };
        }
        let Some(map) = self.dev_map() else {
            return Err("THE SERVER LISTED NO MAPS".into());
        };
        match self.tool {
            DevTool::Drill if self.drills.is_empty() => {
                Err("THIS SERVER LISTED NO AIM DRILLS".into())
            }
            DevTool::Bench if !self.bench_maps.iter().any(|m| m == map) => Err(format!(
                "NO BENCHMARK PATH FOR {} - TRY {}",
                map_title(map),
                self.bench_maps
                    .iter()
                    .map(|m| map_title(m))
                    .collect::<Vec<_>>()
                    .join(", ")
            )),
            _ => Ok(()),
        }
    }

    /// What the tool's button says under its name.
    fn dev_label(&self) -> String {
        if self.tool == DevTool::Server {
            return match self.rooms.get(self.room) {
                Some(room) => format!(
                    "{}  /  {}  /  {} IN",
                    map_title(&room.map),
                    self.mode_name(&room.mode),
                    room.players
                ),
                None => "NO ROOMS".into(),
            };
        }
        let map = self.dev_map().map(map_title).unwrap_or_default();
        match (self.tool, self.dev_drill()) {
            (DevTool::Drill, Some(drill)) => {
                // A drill built on one map runs there whatever is selected.
                let map = drill.map.as_deref().map(map_title).unwrap_or(map);
                format!("{}  /  {}", drill.name.to_uppercase(), map)
            }
            _ => map,
        }
    }

    fn dev_go(&mut self) -> Option<Intent> {
        self.notice = None;
        if let Err(why) = self.dev_ready() {
            self.notice = Some((why, true));
            return None;
        }
        if self.tool == DevTool::Server {
            return self.rooms.get(self.room).map(|room| Intent::Join {
                room: room.id.clone(),
                map: room.map.clone(),
                mode: room.mode.clone(),
            });
        }
        let map = self.dev_map()?.to_string();
        let drill = self.dev_drill().cloned();
        Some(match self.tool {
            DevTool::Drill => {
                let drill = drill?;
                Intent::Tool {
                    tool: DevTool::Drill,
                    map: drill.map.unwrap_or(map),
                    drill: Some(drill.id),
                }
            }
            tool => Intent::Tool {
                tool,
                map,
                drill: None,
            },
        })
    }

    fn step_slot(&mut self, step: i32) -> Option<Intent> {
        let saved = if self.slot < 2 {
            self.loadout.step_weapon(self.side, self.slot, step)
        } else {
            self.loadout.step_agent(self.side, step)
        };
        saved.map(|(key, raw)| Intent::Save(key, raw))
    }

    /// The button: what PLAY does with the current selection.
    fn go(&mut self) -> Option<Intent> {
        if self.tab == Tab::Dev {
            return self.dev_go();
        }
        if matches!(self.tab, Tab::Inventory | Tab::Store) {
            return self.account_go();
        }
        self.notice = None;
        match self.kind {
            PlayKind::Quick => match &self.lobby {
                // In a lobby the search is for all of it, and the leader's.
                Some(lobby) if lobby.leader => Some(Intent::LobbyPlay {
                    maps: self.picked_maps(),
                    mode: self.mode_id().map(str::to_string),
                }),
                Some(lobby) => {
                    self.notice = Some((
                        format!(
                            "ONLY {} CAN START THE SEARCH",
                            lobby.leader_name().to_uppercase()
                        ),
                        true,
                    ));
                    None
                }
                None => Some(Intent::FindMatch {
                    maps: self.picked_maps(),
                    mode: self.mode_id().map(str::to_string),
                    bots: self.bots,
                }),
            },
            PlayKind::Browse => self.rooms.get(self.room).map(|room| Intent::Join {
                room: room.id.clone(),
                map: room.map.clone(),
                mode: room.mode.clone(),
            }),
            PlayKind::Practice => self.map_name().map(|map| Intent::Practice {
                map: map.to_string(),
            }),
        }
    }

    /// Printable text, while the callsign is being typed. Returns whether it
    /// was taken, so the caller knows not to treat the key as a shortcut.
    pub fn text(&mut self, text: &str) -> bool {
        if let Some(code) = self.entering_code.as_mut() {
            // Codes are upper case letters and digits; typed in any case.
            for c in text.chars().filter(|c| c.is_ascii_alphanumeric()) {
                if code.len() < LOBBY_CODE_LEN {
                    code.push(c.to_ascii_uppercase());
                }
            }
            return true;
        }
        let Some(name) = self.editing.as_mut() else {
            return false;
        };
        for c in text.chars().filter(|c| !c.is_control()) {
            if name.chars().count() < CALLSIGN_LEN {
                name.push(c);
            }
        }
        true
    }

    pub fn key(&mut self, code: KeyCode) -> Option<Intent> {
        if let Some(typed) = self.entering_code.as_mut() {
            match code {
                KeyCode::Escape => self.entering_code = None,
                KeyCode::Backspace => {
                    typed.pop();
                }
                KeyCode::Enter | KeyCode::NumpadEnter if typed.len() == LOBBY_CODE_LEN => {
                    let code = typed.clone();
                    self.entering_code = None;
                    return Some(Intent::LobbyJoin(code));
                }
                _ => {}
            }
            return None;
        }
        if let Some(name) = self.editing.as_mut() {
            match code {
                KeyCode::Escape => self.editing = None,
                KeyCode::Backspace => {
                    name.pop();
                }
                KeyCode::Enter | KeyCode::NumpadEnter => {
                    let name = name.trim().to_string();
                    self.editing = None;
                    if !name.is_empty() && name != self.callsign {
                        self.callsign = name.clone();
                        return Some(Intent::SaveCallsign(name));
                    }
                }
                _ => {}
            }
            return None;
        }
        match self.screen {
            Screen::Boot => match code {
                KeyCode::Enter | KeyCode::NumpadEnter if self.boot_failed.is_some() => {
                    Some(Intent::Retry)
                }
                KeyCode::Escape if self.boot_failed.is_some() => Some(Intent::Quit),
                _ => None,
            },
            Screen::Searching => matches!(code, KeyCode::Escape).then_some(Intent::CancelSearch),
            // Nothing to press: a load cannot be half-undone, and the screen
            // does not offer a key that would only appear to.
            Screen::Loading => None,
            Screen::Home => self.home_key(code),
        }
    }

    fn home_key(&mut self, code: KeyCode) -> Option<Intent> {
        match code {
            KeyCode::Escape => {
                self.notice = None;
                None
            }
            KeyCode::Digit1 => self.activate(Target::Tab(Tab::Play)),
            KeyCode::Digit2 => self.activate(Target::Tab(Tab::Loadout)),
            KeyCode::Digit3 => self.activate(Target::Tab(Tab::Inventory)),
            KeyCode::Digit4 => self.activate(Target::Tab(Tab::Store)),
            KeyCode::Digit5 => self.activate(Target::Tab(Tab::Dev)),
            KeyCode::Digit6 => self.activate(Target::Tab(Tab::Settings)),
            KeyCode::Tab => {
                let next = match self.tab {
                    Tab::Play => Tab::Loadout,
                    Tab::Loadout => Tab::Inventory,
                    Tab::Inventory => Tab::Store,
                    Tab::Store => Tab::Dev,
                    _ => Tab::Play,
                };
                self.activate(Target::Tab(next))
            }
            _ if self.tab == Tab::Dev => {
                let at = DevTool::ALL
                    .iter()
                    .position(|t| *t == self.tool)
                    .unwrap_or(0);
                match code {
                    KeyCode::ArrowUp | KeyCode::KeyW => {
                        let n = DevTool::ALL.len();
                        self.activate(Target::Tool(DevTool::ALL[(at + n - 1) % n]))
                    }
                    KeyCode::ArrowDown | KeyCode::KeyS => {
                        self.activate(Target::Tool(DevTool::ALL[(at + 1) % DevTool::ALL.len()]))
                    }
                    KeyCode::ArrowLeft | KeyCode::KeyA => {
                        self.move_map(-1);
                        None
                    }
                    KeyCode::ArrowRight | KeyCode::KeyD => {
                        self.move_map(1);
                        None
                    }
                    KeyCode::BracketLeft => self.activate(Target::Drill(-1)),
                    KeyCode::BracketRight => self.activate(Target::Drill(1)),
                    KeyCode::Enter | KeyCode::NumpadEnter | KeyCode::Space => self.go(),
                    _ => None,
                }
            }
            _ if self.tab == Tab::Loadout => match code {
                KeyCode::ArrowUp | KeyCode::KeyW => {
                    self.activate(Target::Slot((self.slot + 2) % 3))
                }
                KeyCode::ArrowDown | KeyCode::KeyS => {
                    self.activate(Target::Slot((self.slot + 1) % 3))
                }
                KeyCode::ArrowLeft | KeyCode::KeyA => self.step_slot_now(-1),
                KeyCode::ArrowRight | KeyCode::KeyD => self.step_slot_now(1),
                KeyCode::Space => self.activate(Target::Side(1 - self.side)),
                _ => None,
            },
            KeyCode::Enter | KeyCode::NumpadEnter | KeyCode::Space => self.go(),
            KeyCode::F5 | KeyCode::KeyR if self.kind == PlayKind::Browse => {
                Some(Intent::RefreshRooms)
            }
            KeyCode::ArrowUp | KeyCode::KeyW if self.kind == PlayKind::Browse => {
                self.room = self.room.saturating_sub(1);
                None
            }
            KeyCode::ArrowDown | KeyCode::KeyS if self.kind == PlayKind::Browse => {
                if self.room + 1 < self.rooms.len() {
                    self.room += 1;
                }
                None
            }
            KeyCode::ArrowLeft | KeyCode::KeyA => {
                self.move_map(-1);
                None
            }
            KeyCode::ArrowRight | KeyCode::KeyD => {
                self.move_map(1);
                None
            }
            _ => None,
        }
    }

    fn step_slot_now(&mut self, step: i32) -> Option<Intent> {
        self.detail_since = self.clock;
        self.step_slot(step)
    }

    /// Move the map selection along the grid, in reading order. ANY MAP is the
    /// first tile in quick play and absent in practice.
    fn move_map(&mut self, delta: i32) {
        let play = self.tab != Tab::Dev;
        if self.maps.is_empty() || (play && self.kind == PlayKind::Browse) {
            return;
        }
        let any = play && self.kind == PlayKind::Quick;
        let count = self.maps.len() as i32 + i32::from(any);
        let at = match self.map {
            None => 0,
            Some(i) => i as i32 + i32::from(any),
        };
        let next = (at + delta).rem_euclid(count);
        self.map = if any && next == 0 {
            None
        } else {
            Some((next - i32::from(any)) as usize)
        };
        if self.picking() {
            // The keys choose one map; toggling several is the pointer's.
            self.picks = self.map.into_iter().collect();
        }
    }

    // -- layout ---------------------------------------------------------------

    /// Everything clickable, in window pixels. The painter draws from this too.
    pub fn hits(&self, kit: Option<&TextKit>, width: f32, height: f32) -> Vec<(Target, Hit)> {
        let k = unit(height);
        let mut out = Vec::new();
        match self.screen {
            Screen::Boot => {
                if self.boot_failed.is_some() {
                    out.push((
                        Target::Retry,
                        button_at(MARGIN * k, height - 150.0 * k, 220.0, k),
                    ));
                    out.push((
                        Target::Quit,
                        button_at((MARGIN + 236.0) * k, height - 150.0 * k, 160.0, k),
                    ));
                }
            }
            Screen::Searching => {
                let panel = search_panel(width, height, k);
                out.push((
                    Target::Cancel,
                    Hit {
                        x: panel.x + panel.w - 176.0 * k,
                        y: panel.y + panel.h - 60.0 * k,
                        w: 152.0 * k,
                        h: 40.0 * k,
                    },
                ));
            }
            Screen::Loading => {}
            Screen::Home => self.home_hits(kit, width, height, k, &mut out),
        }
        out
    }

    fn home_hits(
        &self,
        kit: Option<&TextKit>,
        width: f32,
        height: f32,
        k: f32,
        out: &mut Vec<(Target, Hit)>,
    ) {
        let right = width - MARGIN * k;

        // The top bar: tabs after the wordmark, identity on the right.
        let mut x = MARGIN * k + measure(kit, Face::Display, WORDMARK, 2.6 * k) + 44.0 * k;
        for tab in Tab::ALL {
            let w = measure(kit, Face::Display, tab.label(), 1.7 * k) + 28.0 * k;
            out.push((
                Target::Tab(tab),
                Hit {
                    x,
                    y: 0.0,
                    w,
                    h: TOP * k,
                },
            ));
            x += w;
        }
        let quit_w = measure(kit, Face::Display, "QUIT", 1.5 * k) + 24.0 * k;
        out.push((
            Target::Quit,
            Hit {
                x: right - quit_w,
                y: 14.0 * k,
                w: quit_w,
                h: 36.0 * k,
            },
        ));
        out.push((
            Target::Callsign,
            Hit {
                // Narrow: six tabs reach further right than four did, and
                // the name and status are right-aligned inside it anyway.
                x: right - quit_w - 216.0 * k,
                y: 10.0 * k,
                w: 200.0 * k,
                h: 44.0 * k,
            },
        ));
        if self.notice.is_some() {
            out.push((
                Target::Dismiss,
                Hit {
                    x: MARGIN * k,
                    y: (TOP + 10.0) * k,
                    w: right - MARGIN * k,
                    h: 30.0 * k,
                },
            ));
        }

        let top = self.content_top(k);
        match self.tab {
            Tab::Play | Tab::Settings => self.play_hits(width, height, k, top, out),
            Tab::Loadout => self.loadout_hits(width, k, top, out),
            Tab::Dev => self.dev_hits(width, height, k, top, out),
            Tab::Inventory | Tab::Store => self.account_hits(width, height, k, top, out),
        }
    }

    /// DEV TOOLS, on the PLAY tab's grid: tools down the left where the ways
    /// in are, the same map tiles, the same button.
    fn dev_hits(&self, width: f32, height: f32, k: f32, top: f32, out: &mut Vec<(Target, Hit)>) {
        let left = MARGIN * k;
        let right = width - MARGIN * k;
        let mut y = top + 22.0 * k;
        // Five tools where PLAY has three ways in: shorter cards, so the
        // column still ends above the foot of a 720p window.
        for tool in DevTool::ALL {
            out.push((
                Target::Tool(tool),
                Hit {
                    x: left,
                    y,
                    w: COLUMN * k,
                    h: 52.0 * k,
                },
            ));
            y += 58.0 * k;
        }
        y += 28.0 * k;
        if self.tool == DevTool::Server {
            out.push((
                Target::Refresh,
                Hit {
                    x: left,
                    y,
                    w: COLUMN * k,
                    h: 36.0 * k,
                },
            ));
            y += 64.0 * k;
        }
        if self.tool == DevTool::Drill {
            for (target, x) in [
                (Target::Drill(-1), left),
                (Target::Drill(1), left + (COLUMN - 36.0) * k),
            ] {
                out.push((
                    target,
                    Hit {
                        x,
                        y,
                        w: 36.0 * k,
                        h: 36.0 * k,
                    },
                ));
            }
            y += 64.0 * k;
        }
        for i in 0..DEV_OVERLAYS.len() {
            out.push((
                Target::Overlay(i),
                Hit {
                    x: left,
                    y: y + i as f32 * 34.0 * k,
                    w: COLUMN * k,
                    h: 30.0 * k,
                },
            ));
        }

        let area_x = left + (COLUMN + 32.0) * k;
        let area_y = top + 22.0 * k;
        let (tile_w, tile_h, gap) = (188.0 * k, 92.0 * k, 10.0 * k);
        let columns = (((right - area_x) + gap) / (tile_w + gap)).floor().max(1.0) as usize;
        if self.tool == DevTool::Server {
            // The room list where the map tiles are, on the left of the area;
            // the roster (drawn, not clickable) has the rest.
            let row_h = 38.0 * k;
            let floor = height - (MARGIN + GO_H + 20.0) * k;
            for i in 0..self.rooms.len() {
                let y = area_y + 30.0 * k + i as f32 * row_h;
                if y + row_h > floor {
                    break;
                }
                out.push((
                    Target::Room(i),
                    Hit {
                        x: area_x,
                        y,
                        w: (right - area_x) * SERVER_SPLIT,
                        h: row_h - 4.0 * k,
                    },
                ));
            }
        }
        let tiles = if self.tool == DevTool::Server {
            0
        } else {
            self.maps.len()
        };
        for i in 0..tiles {
            let (col, row) = (i % columns, i / columns);
            out.push((
                Target::Map(Some(i)),
                Hit {
                    x: area_x + col as f32 * (tile_w + gap),
                    y: area_y + row as f32 * (tile_h + gap),
                    w: tile_w,
                    h: tile_h,
                },
            ));
        }
        out.push((
            Target::Go,
            Hit {
                x: right - GO_W * k,
                y: height - (MARGIN + GO_H) * k,
                w: GO_W * k,
                h: GO_H * k,
            },
        ));
    }

    /// Where the tab's content starts: below the bar, and below a notice when
    /// one is showing, so a message never covers what it is about.
    fn content_top(&self, k: f32) -> f32 {
        (TOP + if self.notice.is_some() { 64.0 } else { 34.0 }) * k
    }

    fn play_hits(&self, width: f32, height: f32, k: f32, top: f32, out: &mut Vec<(Target, Hit)>) {
        let left = MARGIN * k;
        let right = width - MARGIN * k;
        let mut y = top + 22.0 * k;
        for kind in PlayKind::ALL {
            out.push((
                Target::Kind(kind),
                Hit {
                    x: left,
                    y,
                    w: COLUMN * k,
                    h: 62.0 * k,
                },
            ));
            y += 70.0 * k;
        }
        y += 34.0 * k;
        let stepper = |y: f32, minus: Target, plus: Target, out: &mut Vec<(Target, Hit)>| {
            out.push((
                minus,
                Hit {
                    x: left,
                    y,
                    w: 36.0 * k,
                    h: 36.0 * k,
                },
            ));
            out.push((
                plus,
                Hit {
                    x: left + (COLUMN - 36.0) * k,
                    y,
                    w: 36.0 * k,
                    h: 36.0 * k,
                },
            ));
        };
        match self.kind {
            PlayKind::Quick => {
                stepper(y, Target::Mode(-1), Target::Mode(1), out);
                stepper(y + 64.0 * k, Target::Bots(-1), Target::Bots(1), out);
                // The lobby, under the steppers: two buttons to get into
                // one, or its code and a way out once in.
                let ly = y + 134.0 * k;
                if self.lobby.is_some() {
                    out.push((
                        Target::LobbyLeave,
                        Hit {
                            x: left + (COLUMN - 84.0) * k,
                            y: ly,
                            w: 84.0 * k,
                            h: 30.0 * k,
                        },
                    ));
                } else {
                    let half = (COLUMN - 8.0) * 0.5 * k;
                    out.push((
                        Target::LobbyCreate,
                        Hit {
                            x: left,
                            y: ly,
                            w: half,
                            h: 36.0 * k,
                        },
                    ));
                    out.push((
                        Target::LobbyJoin,
                        Hit {
                            x: left + half + 8.0 * k,
                            y: ly,
                            w: half,
                            h: 36.0 * k,
                        },
                    ));
                }
            }
            PlayKind::Browse => out.push((
                Target::Refresh,
                Hit {
                    x: left,
                    y,
                    w: COLUMN * k,
                    h: 36.0 * k,
                },
            )),
            PlayKind::Practice => {}
        }

        let area_x = left + (COLUMN + 32.0) * k;
        let area_y = top + 22.0 * k;
        if self.kind == PlayKind::Browse {
            let row_h = 38.0 * k;
            let floor = height - (MARGIN + GO_H + 20.0) * k;
            for i in 0..self.rooms.len() {
                let y = area_y + 30.0 * k + i as f32 * row_h;
                if y + row_h > floor {
                    break;
                }
                out.push((
                    Target::Room(i),
                    Hit {
                        x: area_x,
                        y,
                        w: right - area_x,
                        h: row_h - 4.0 * k,
                    },
                ));
            }
        } else {
            let any = self.kind == PlayKind::Quick;
            let (tile_w, tile_h, gap) = (188.0 * k, 92.0 * k, 10.0 * k);
            let columns = (((right - area_x) + gap) / (tile_w + gap)).floor().max(1.0) as usize;
            let count = self.maps.len() + usize::from(any);
            for i in 0..count {
                let (col, row) = (i % columns, i / columns);
                let map = if any { i.checked_sub(1) } else { Some(i) };
                out.push((
                    Target::Map(map),
                    Hit {
                        x: area_x + col as f32 * (tile_w + gap),
                        y: area_y + row as f32 * (tile_h + gap),
                        w: tile_w,
                        h: tile_h,
                    },
                ));
            }
        }

        out.push((
            Target::Go,
            Hit {
                x: right - GO_W * k,
                y: height - (MARGIN + GO_H) * k,
                w: GO_W * k,
                h: GO_H * k,
            },
        ));
    }

    fn loadout_hits(&self, _width: f32, k: f32, top: f32, out: &mut Vec<(Target, Hit)>) {
        let left = MARGIN * k;
        for side in 0..2 {
            out.push((
                Target::Side(side),
                Hit {
                    x: left + side as f32 * (COLUMN * 0.5 + 4.0) * k,
                    y: top,
                    w: (COLUMN * 0.5 - 4.0) * k,
                    h: 40.0 * k,
                },
            ));
        }
        for slot in 0..3 {
            let y = top + (56.0 + slot as f32 * 92.0) * k;
            out.push((
                Target::Step(slot, -1),
                Hit {
                    x: left,
                    y: y + 40.0 * k,
                    w: 36.0 * k,
                    h: 36.0 * k,
                },
            ));
            out.push((
                Target::Step(slot, 1),
                Hit {
                    x: left + (COLUMN - 36.0) * k,
                    y: y + 40.0 * k,
                    w: 36.0 * k,
                    h: 36.0 * k,
                },
            ));
            out.push((
                Target::Slot(slot),
                Hit {
                    x: left,
                    y,
                    w: COLUMN * k,
                    h: 84.0 * k,
                },
            ));
        }
    }

    // -- drawing --------------------------------------------------------------

    pub fn paint(
        &self,
        kit: Option<&TextKit>,
        width: f32,
        height: f32,
        out: &mut Vec<OverlayVertex>,
    ) {
        let hits = self.hits(kit, width, height);
        let mut p = Painter::with_text(out, width, height, kit);
        let k = unit(height);
        backdrop(&mut p, width, height, k, self.backdrop_map().is_some());
        match self.screen {
            Screen::Boot => self.paint_boot(&mut p, &hits, width, height, k),
            Screen::Home => self.paint_home(&mut p, &hits, width, height, k),
            Screen::Searching => {
                self.paint_home(&mut p, &[], width, height, k);
                self.paint_search(&mut p, &hits, width, height, k);
            }
            Screen::Loading => self.paint_loading(&mut p, width, height, k),
        }
    }

    fn hovered(&self, rect: &Hit) -> bool {
        rect.contains(self.pointer.0, self.pointer.1)
    }

    fn paint_boot(&self, p: &mut Painter, hits: &[(Target, Hit)], _w: f32, height: f32, k: f32) {
        let x = MARGIN * k;
        p.text(x, 96.0 * k, 5.2 * k, theme::TEXT, WORDMARK);
        p.rect(x, 146.0 * k, 96.0 * k, 2.0, theme::ACCENT);
        let line = match &self.boot_failed {
            Some(_) => format!("COULD NOT REACH {}", self.server.to_uppercase()),
            None => format!("CONNECTING TO {}", self.server.to_uppercase()),
        };
        p.num(
            x,
            164.0 * k,
            1.5 * k,
            if self.boot_failed.is_some() {
                theme::RED
            } else {
                theme::TEXT_DIM
            },
            &line,
        );
        steps(p, &self.steps, x, 214.0 * k, k, self.clock);
        if let Some(why) = &self.boot_failed {
            p.num(x, height - 196.0 * k, 1.35 * k, theme::TEXT_DIM, why);
            for (target, rect) in hits {
                let (label, primary) = match target {
                    Target::Retry => ("TRY AGAIN", true),
                    _ => ("QUIT", false),
                };
                button(p, rect, k, label, primary, self.hovered(rect));
            }
        } else {
            bar(p, x, height - 96.0 * k, 420.0 * k, 4.0 * k, self.progress());
        }
    }

    fn paint_home(&self, p: &mut Painter, hits: &[(Target, Hit)], width: f32, height: f32, k: f32) {
        let left = MARGIN * k;
        let right = width - MARGIN * k;

        // ---- the top bar ----------------------------------------------------
        p.gradient_v(0.0, 0.0, width, TOP * k, theme::panel(0.82), theme::panel(0.5));
        p.rect(0.0, TOP * k, width, 1.0, theme::FAINT);
        // The accent runs out along the bar's foot from the wordmark's side
        // and is gone by the middle: the bar has a start.
        p.gradient_h(
            0.0,
            TOP * k - 1.0,
            width * 0.5,
            2.0,
            theme::with_alpha(theme::ACCENT, 0.7),
            theme::with_alpha(theme::ACCENT, 0.0),
        );
        // The mark: one leaning bar in the margin, the slab's own angle.
        p.banner(left - 20.0 * k, 21.0 * k, 13.0 * k, 21.0 * k, 5.0 * k, theme::ACCENT, None);
        p.text(left, 22.0 * k, 2.6 * k, theme::TEXT, WORDMARK);
        for (target, rect) in hits {
            match target {
                Target::Tab(tab) => {
                    let active = *tab == self.tab;
                    let ink = if active {
                        theme::TEXT
                    } else if self.hovered(rect) {
                        theme::with_alpha(theme::TEXT, 0.8)
                    } else {
                        theme::TEXT_DIM
                    };
                    if active {
                        // Lit from its own underline: the tab you are on is a
                        // column of light, not just a line under a word.
                        p.gradient_v(
                            rect.x + 4.0 * k,
                            0.0,
                            rect.w - 8.0 * k,
                            TOP * k,
                            theme::with_alpha(theme::ACCENT, 0.0),
                            theme::with_alpha(theme::ACCENT, 0.20),
                        );
                        p.rect(
                            rect.x + 4.0 * k,
                            TOP * k - 3.0,
                            rect.w - 8.0 * k,
                            3.0,
                            theme::ACCENT,
                        );
                    } else if self.hovered(rect) {
                        p.gradient_v(
                            rect.x + 4.0 * k,
                            0.0,
                            rect.w - 8.0 * k,
                            TOP * k,
                            [1.0, 1.0, 1.0, 0.0],
                            [1.0, 1.0, 1.0, 0.06],
                        );
                    }
                    p.text(rect.x + 14.0 * k, 26.0 * k, 1.7 * k, ink, tab.label());
                }
                Target::Quit => {
                    let hot = self.hovered(rect);
                    if hot {
                        p.rect(
                            rect.x,
                            rect.y,
                            rect.w,
                            rect.h,
                            theme::with_alpha(theme::RED, 0.18),
                        );
                    }
                    p.text(
                        rect.x + 12.0 * k,
                        rect.y + 12.0 * k,
                        1.5 * k,
                        if hot { theme::RED } else { theme::TEXT_DIM },
                        "QUIT",
                    );
                }
                Target::Callsign => self.paint_identity(p, rect, k),
                Target::Dismiss => {
                    let (message, error) = self.notice.as_ref().expect("a notice to dismiss");
                    let ink = if *error { theme::RED } else { theme::GREEN };
                    p.rect(rect.x, rect.y, rect.w, rect.h, theme::with_alpha(ink, 0.12));
                    p.rect(rect.x, rect.y, 2.0, rect.h, ink);
                    p.num(rect.x + 14.0 * k, rect.y + 10.0 * k, 1.35 * k, ink, message);
                    p.text_right(
                        rect.x + rect.w - 12.0 * k,
                        rect.y + 10.0 * k,
                        1.2 * k,
                        theme::TEXT_DIM,
                        "ESC DISMISSES",
                    );
                }
                _ => {}
            }
        }

        let top = self.content_top(k);
        match self.tab {
            Tab::Play | Tab::Settings => self.paint_play(p, hits, width, height, k, top),
            Tab::Loadout => self.paint_loadout(p, hits, width, height, k, top),
            Tab::Dev => self.paint_dev(p, hits, k, top),
            Tab::Inventory | Tab::Store => self.paint_account(p, hits, width, k, top),
        }

        // ---- the foot -------------------------------------------------------
        let hint = match self.tab {
            Tab::Loadout => HINT_LOADOUT,
            Tab::Dev => HINT_DEV,
            _ => HINT_PLAY,
        };
        p.rect(
            left,
            height - (MARGIN + 22.0) * k,
            (right - left - (GO_W + 24.0) * k).max(0.0),
            1.0,
            [1.0, 1.0, 1.0, 0.07],
        );
        p.text(
            left,
            height - (MARGIN + 8.0) * k,
            1.15 * k,
            theme::FAINT,
            hint,
        );
    }

    /// Who you are and where you are playing, top right.
    fn paint_identity(&self, p: &mut Painter, rect: &Hit, k: f32) {
        let right = rect.x + rect.w;
        let hot = self.hovered(rect);
        match &self.editing {
            Some(name) => {
                p.rect(rect.x, rect.y, rect.w, rect.h, [0.09, 0.11, 0.15, 1.0]);
                p.rect(rect.x, rect.y + rect.h - 2.0, rect.w, 2.0, theme::AMBER);
                p.text(
                    rect.x + 10.0 * k,
                    rect.y + 6.0 * k,
                    1.0 * k,
                    theme::TEXT_DIM,
                    "CALLSIGN - ENTER SAVES",
                );
                p.name(
                    rect.x + 10.0 * k,
                    rect.y + 22.0 * k,
                    1.7 * k,
                    theme::AMBER,
                    name,
                );
                let caret = rect.x + 10.0 * k + p.name_width(name, 1.7 * k) + 2.0 * k;
                // Blinks on the clock, so an idle field still looks alive.
                if (self.clock * 2.0) as i32 % 2 == 0 {
                    p.rect(caret, rect.y + 20.0 * k, 2.0 * k, 16.0 * k, theme::AMBER);
                }
            }
            None => {
                p.rect(
                    rect.x,
                    rect.y,
                    rect.w,
                    rect.h,
                    [1.0, 1.0, 1.0, if hot { 0.07 } else { 0.03 }],
                );
                p.rect(
                    right - 2.0,
                    rect.y,
                    2.0,
                    rect.h,
                    theme::with_alpha(theme::AMBER, if hot { 0.95 } else { 0.55 }),
                );
                // Signed in, the name people see is the account's; the
                // callsign is what a guest goes by.
                let shown = self
                    .account
                    .as_ref()
                    .map_or(self.callsign.as_str(), |a| a.name.as_str());
                let w = p.name_width(shown, 1.7 * k);
                p.name(
                    right - w - 8.0 * k,
                    rect.y + 6.0 * k,
                    1.7 * k,
                    theme::AMBER,
                    shown,
                );
                let (rooms, players) = self.population();
                // Short: six tabs leave this corner less room than four did,
                // and the server's address is on the boot screen.
                let status = format!(
                    "{}  /  {} {}  /  {} PLAYING",
                    if self.account.is_some() {
                        "SIGNED IN"
                    } else {
                        "GUEST"
                    },
                    rooms,
                    if rooms == 1 { "ROOM" } else { "ROOMS" },
                    players
                );
                let sw = p.num_width(&status, 1.05 * k);
                p.num(
                    right - sw - 8.0 * k,
                    rect.y + 28.0 * k,
                    1.05 * k,
                    theme::TEXT_DIM,
                    &status,
                );
                // The link is up: this line only exists once boot reached it.
                p.rect(
                    right - sw - 20.0 * k,
                    rect.y + 30.0 * k,
                    5.0 * k,
                    5.0 * k,
                    theme::GREEN,
                );
            }
        }
    }

    fn paint_play(
        &self,
        p: &mut Painter,
        hits: &[(Target, Hit)],
        width: f32,
        height: f32,
        k: f32,
        top: f32,
    ) {
        let left = MARGIN * k;
        let right = width - MARGIN * k;
        let area_x = left + (COLUMN + 32.0) * k;
        heading(p, left, top, k, "HOW");
        self.paint_lobby(p, hits, k);
        heading(
            p,
            area_x,
            top,
            k,
            if self.kind == PlayKind::Browse {
                "OPEN ROOMS"
            } else {
                "WHERE"
            },
        );

        let mut tile = 0;
        for (target, rect) in hits {
            let hot = self.hovered(rect);
            match target {
                Target::Kind(kind) => {
                    let active = *kind == self.kind;
                    let index = PlayKind::ALL.iter().position(|c| c == kind).unwrap_or(0);
                    let a = self.arrive(index);
                    card(p, rect, 10.0 * k, active, hot, a);
                    p.text(
                        rect.x + 16.0 * k,
                        rect.y + 14.0 * k,
                        1.8 * k,
                        theme::faded(if active { theme::TEXT } else { theme::TEXT_DIM }, a),
                        kind.title(),
                    );
                    p.num(
                        rect.x + 16.0 * k,
                        rect.y + 38.0 * k,
                        1.1 * k,
                        theme::faded(theme::FAINT, a * 1.6),
                        &kind.about().to_uppercase(),
                    );
                }
                Target::Mode(step) | Target::Bots(step) => {
                    arrow(p, rect, k, *step, hot);
                    // The value sits between its two arrows; drawn once, with
                    // the left one, so it is not painted twice.
                    if *step < 0 {
                        let (label, value) = match target {
                            Target::Mode(_) => ("GAME", self.mode_label()),
                            _ => (
                                "BOTS IN A NEW ROOM",
                                if self.bots == 0 {
                                    "NONE".into()
                                } else {
                                    self.bots.to_string()
                                },
                            ),
                        };
                        p.text(rect.x, rect.y - 16.0 * k, 1.05 * k, theme::FAINT, label);
                        let w = COLUMN * k - rect.w * 2.0;
                        p.rect(rect.x + rect.w, rect.y, w, rect.h, [1.0, 1.0, 1.0, 0.04]);
                        let vw = p.text_width(&value, 1.45 * k);
                        p.text(
                            rect.x + rect.w + (w - vw) * 0.5,
                            rect.y + 12.0 * k,
                            1.45 * k,
                            theme::TEXT,
                            &value,
                        );
                    }
                }
                Target::Refresh => button(p, rect, k, "REFRESH THE LIST", false, hot),
                Target::Map(map) => {
                    let active = self.map_active(*map);
                    let a = self.arrive(tile + 3);
                    tile += 1;
                    card(p, rect, 12.0 * k, active, hot, a);
                    self.dress_tile(p, rect, k, *map, active, a);
                    let (title, meta) = match map.and_then(|i| self.maps.get(i)) {
                        Some(entry) => {
                            let here: Vec<&RoomEntry> =
                                self.rooms.iter().filter(|r| r.map == entry.name).collect();
                            let playing: u32 = here.iter().map(|r| r.players).sum();
                            let meta = if self.kind == PlayKind::Practice || here.is_empty() {
                                bytes_label(entry.size)
                            } else {
                                format!("{} PLAYING  /  {}", playing, bytes_label(entry.size))
                            };
                            (map_title(&entry.name), meta)
                        }
                        None => ("ANY MAP".to_string(), "WHEREVER PEOPLE ARE".to_string()),
                    };
                    p.text(
                        rect.x + 14.0 * k,
                        rect.y + 18.0 * k,
                        2.0 * k,
                        theme::faded(if active { theme::TEXT } else { theme::TEXT_DIM }, a),
                        &title,
                    );
                    // A green dot ahead of the line when somebody is on the
                    // map right now: found by colour before it is read.
                    let live = meta.contains("PLAYING");
                    if live {
                        p.rect(
                            rect.x + 14.0 * k,
                            rect.y + rect.h - 21.0 * k,
                            5.0 * k,
                            5.0 * k,
                            theme::faded(theme::GREEN, a),
                        );
                    }
                    p.num(
                        rect.x + if live { 26.0 } else { 14.0 } * k,
                        rect.y + rect.h - 24.0 * k,
                        1.05 * k,
                        theme::faded(if active { theme::AMBER } else { theme::FAINT }, a * 1.5),
                        &meta,
                    );
                }
                Target::Room(i) => self.paint_room(p, rect, k, *i, hot),
                Target::Go => self.paint_go(p, rect, k, hot),
                _ => {}
            }
        }

        if self.kind == PlayKind::Browse {
            let y = top + 22.0 * k;
            // The column heads, in the muted face, over the rows they name.
            let w = right - area_x;
            p.num(area_x + 14.0 * k, y + 6.0 * k, 1.0 * k, theme::FAINT, "MAP");
            p.num(
                area_x + w * 0.36,
                y + 6.0 * k,
                1.0 * k,
                theme::FAINT,
                "GAME",
            );
            p.num(
                area_x + w * 0.72,
                y + 6.0 * k,
                1.0 * k,
                theme::FAINT,
                "PLAYERS",
            );
            p.num_right(right - 14.0 * k, y + 6.0 * k, 1.0 * k, theme::FAINT, "ROOM");
            p.rule(area_x, y + 24.0 * k, w, theme::FAINT, theme::ACCENT);
            if self.rooms.is_empty() {
                let (line, ink) = match &self.rooms_error {
                    Some(why) => (
                        format!("THE ROOM LIST DID NOT LOAD - {}", why.to_uppercase()),
                        theme::RED,
                    ),
                    None => (
                        "NO ROOMS ARE OPEN - QUICK PLAY OPENS ONE".to_string(),
                        theme::TEXT_DIM,
                    ),
                };
                p.num(area_x + 14.0 * k, y + 46.0 * k, 1.35 * k, ink, &line);
            }
        } else if self.maps.is_empty() {
            p.num(
                area_x,
                top + 40.0 * k,
                1.35 * k,
                theme::RED,
                "THE SERVER LISTED NO MAPS",
            );
        }
        let _ = height;
    }

    /// The roster to show: the one fetched, if it is still the selected
    /// room's. One for a room that is no longer selected is somebody else's
    /// scoreboard under this room's name.
    fn shown_roster(&self) -> Option<&RoomDetail> {
        let selected = self.rooms.get(self.room)?;
        self.roster.as_ref().filter(|d| d.id == selected.id)
    }

    /// Who is in the server view's selected room, right of the room list.
    fn paint_roster(&self, p: &mut Painter, hits: &[(Target, Hit)], k: f32, top: f32) {
        let Some((_, go)) = hits.iter().find(|(t, _)| *t == Target::Go) else {
            return;
        };
        let right = go.x + go.w;
        let area_x = (MARGIN + COLUMN + 32.0) * k;
        let x = area_x + (right - area_x) * SERVER_SPLIT + 20.0 * k;
        let floor = go.y - 20.0 * k;
        heading(p, x, top, k, "IN THE ROOM");
        let mut y = top + 52.0 * k;
        let selected = self.rooms.get(self.room);
        let detail = self.shown_roster();
        let Some(detail) = detail else {
            let say = match (selected, &self.rooms_error) {
                (_, Some(_)) => "THE SERVER DID NOT ANSWER",
                (None, _) => "NO ROOM IS RUNNING",
                (Some(_), _) => "ASKING WHO IS IN",
            };
            p.num(x, y, 1.1 * k, theme::FAINT, say);
            return;
        };
        if detail.roster.is_empty() {
            p.num(x, y, 1.1 * k, theme::FAINT, "EMPTY - IT CLOSES IN A MINUTE");
            return;
        }
        p.num(x, y - 18.0 * k, 1.0 * k, theme::FAINT, "CALLSIGN");
        p.num_right(right, y - 18.0 * k, 1.0 * k, theme::FAINT, "K / D");
        for (i, row) in detail.roster.iter().enumerate() {
            if y + 24.0 * k > floor {
                p.num(
                    x,
                    y,
                    1.0 * k,
                    theme::FAINT,
                    &format!("AND {} MORE", detail.roster.len() - i),
                );
                break;
            }
            let a = self.arrive(i);
            let host = row.id == detail.host;
            let ink = if row.bot {
                theme::TEXT_DIM
            } else {
                theme::TEXT
            };
            p.rect(
                x,
                y - 4.0 * k,
                right - x,
                22.0 * k,
                [1.0, 1.0, 1.0, 0.03 * a],
            );
            if host {
                p.rect(x, y - 4.0 * k, 2.0, 22.0 * k, theme::AMBER);
            }
            p.text(x + 10.0 * k, y, 1.25 * k, theme::faded(ink, a), &row.name);
            let tag = match (host, row.bot) {
                (true, _) => "HOST",
                (_, true) => "BOT",
                _ => "",
            };
            p.num(
                x + (right - x) * 0.56,
                y + 1.0 * k,
                1.0 * k,
                theme::faded(if host { theme::AMBER } else { theme::FAINT }, a * 1.5),
                tag,
            );
            p.num_right(
                right - 8.0 * k,
                y,
                1.2 * k,
                theme::faded(theme::TEXT_DIM, a),
                &format!("{} / {}", row.kills, row.deaths),
            );
            y += 26.0 * k;
        }
    }

    /// One row of a room list: map, game, seats, id.
    fn paint_room(&self, p: &mut Painter, rect: &Hit, k: f32, index: usize, hot: bool) {
        let i = &index;
        let Some(_) = self.rooms.get(index) else {
            return;
        };
        let room = &self.rooms[*i];
        let active = *i == self.room;
        let a = self.arrive(*i + 3);
        p.rect(
            rect.x,
            rect.y,
            rect.w,
            rect.h,
            if active {
                theme::with_alpha(theme::ACCENT, 0.2 * a)
            } else if hot {
                [1.0, 1.0, 1.0, 0.06 * a]
            } else {
                [1.0, 1.0, 1.0, 0.03 * a]
            },
        );
        if active {
            p.rect(rect.x, rect.y, 2.0, rect.h, theme::ACCENT);
        }
        let y = rect.y + 11.0 * k;
        let ink = theme::faded(if active { theme::TEXT } else { theme::TEXT_DIM }, a);
        p.text(rect.x + 14.0 * k, y, 1.5 * k, ink, &map_title(&room.map));
        p.text(
            rect.x + rect.w * 0.36,
            y,
            1.3 * k,
            ink,
            &self.mode_name(&room.mode),
        );
        let seats = format!("{} / {}", room.players, room.seats());
        p.num(
            rect.x + rect.w * 0.72,
            y,
            1.4 * k,
            theme::faded(
                if room.open() {
                    theme::GREEN
                } else {
                    theme::RED
                },
                a,
            ),
            &seats,
        );
        // The id is the least of it: in the server view's narrower row it
        // would run into the seats, so it gives way.
        if rect.w >= 560.0 * k {
            p.num_right(
                rect.x + rect.w - 14.0 * k,
                y + 1.0 * k,
                1.1 * k,
                theme::faded(theme::FAINT, a * 1.5),
                &room.id.chars().take(8).collect::<String>(),
            );
        }
    }

    fn paint_dev(&self, p: &mut Painter, hits: &[(Target, Hit)], k: f32, top: f32) {
        let left = MARGIN * k;
        let area_x = left + (COLUMN + 32.0) * k;
        heading(p, left, top, k, "TOOL");
        heading(
            p,
            area_x,
            top,
            k,
            if self.tool == DevTool::Server {
                "ROOMS"
            } else {
                "ON"
            },
        );
        if self.tool == DevTool::Server {
            self.paint_roster(p, hits, k, top);
        }
        let selected = self.map.unwrap_or(0);
        let mut tile = 0;
        for (target, rect) in hits {
            let hot = self.hovered(rect);
            match target {
                Target::Tool(tool) => {
                    let active = *tool == self.tool;
                    let index = DevTool::ALL.iter().position(|t| t == tool).unwrap_or(0);
                    let a = self.arrive(index);
                    card(p, rect, 10.0 * k, active, hot, a);
                    p.text(
                        rect.x + 16.0 * k,
                        rect.y + 10.0 * k,
                        1.7 * k,
                        theme::faded(if active { theme::TEXT } else { theme::TEXT_DIM }, a),
                        tool.title(),
                    );
                    p.num(
                        rect.x + 16.0 * k,
                        rect.y + 33.0 * k,
                        1.1 * k,
                        theme::faded(theme::FAINT, a * 1.6),
                        &tool.about().to_uppercase(),
                    );
                }
                Target::Drill(step) => {
                    arrow(p, rect, k, *step, hot);
                    if *step < 0 {
                        let value = self
                            .dev_drill()
                            .map(|d| d.name.to_uppercase())
                            .unwrap_or_else(|| "NONE LISTED".into());
                        p.text(rect.x, rect.y - 16.0 * k, 1.05 * k, theme::FAINT, "DRILL");
                        let w = COLUMN * k - rect.w * 2.0;
                        p.rect(rect.x + rect.w, rect.y, w, rect.h, [1.0, 1.0, 1.0, 0.04]);
                        let vw = p.text_width(&value, 1.3 * k);
                        p.text(
                            rect.x + rect.w + (w - vw) * 0.5,
                            rect.y + 13.0 * k,
                            1.3 * k,
                            theme::TEXT,
                            &value,
                        );
                    }
                }
                Target::Refresh => button(p, rect, k, "REFRESH THE LIST", false, hot),
                Target::Room(i) => self.paint_room(p, rect, k, *i, hot),
                Target::Overlay(i) => {
                    let on = self.overlays[*i];
                    if *i == 0 {
                        p.text(
                            rect.x,
                            rect.y - 16.0 * k,
                            1.05 * k,
                            theme::FAINT,
                            "DRAWN IN THE NEXT MATCH",
                        );
                    }
                    p.rect(
                        rect.x,
                        rect.y,
                        rect.w,
                        rect.h,
                        [1.0, 1.0, 1.0, if hot { 0.07 } else { 0.035 }],
                    );
                    // The switch: a frame, filled when it is on.
                    let (bx, by, b) = (rect.x + 10.0 * k, rect.y + 8.0 * k, 14.0 * k);
                    p.rect(bx, by, b, b, theme::with_alpha(theme::TEXT_DIM, 0.5));
                    p.rect(
                        bx + 2.0,
                        by + 2.0,
                        b - 4.0,
                        b - 4.0,
                        if on {
                            theme::ACCENT
                        } else {
                            [0.02, 0.03, 0.05, 1.0]
                        },
                    );
                    p.text(
                        rect.x + 36.0 * k,
                        rect.y + 9.0 * k,
                        1.25 * k,
                        if on { theme::TEXT } else { theme::TEXT_DIM },
                        DEV_OVERLAYS[*i],
                    );
                    p.text_right(
                        rect.x + rect.w - 10.0 * k,
                        rect.y + 10.0 * k,
                        1.05 * k,
                        if on { theme::AMBER } else { theme::FAINT },
                        if on { "ON" } else { "OFF" },
                    );
                }
                Target::Map(Some(i)) => {
                    let active = *i == selected;
                    let a = self.arrive(tile + 3);
                    tile += 1;
                    card(p, rect, 12.0 * k, active, hot, a);
                    self.dress_tile(p, rect, k, Some(*i), active, a);
                    let Some(entry) = self.maps.get(*i) else {
                        continue;
                    };
                    let meta = match self.tool {
                        DevTool::Bench if self.bench_maps.contains(&entry.name) => {
                            "BENCHMARK PATH".to_string()
                        }
                        DevTool::Bench => "NO PATH".to_string(),
                        _ => bytes_label(entry.size),
                    };
                    p.text(
                        rect.x + 14.0 * k,
                        rect.y + 18.0 * k,
                        2.0 * k,
                        theme::faded(if active { theme::TEXT } else { theme::TEXT_DIM }, a),
                        &map_title(&entry.name),
                    );
                    // A green dot ahead of the line when somebody is on the
                    // map right now: found by colour before it is read.
                    let live = meta.contains("PLAYING");
                    if live {
                        p.rect(
                            rect.x + 14.0 * k,
                            rect.y + rect.h - 21.0 * k,
                            5.0 * k,
                            5.0 * k,
                            theme::faded(theme::GREEN, a),
                        );
                    }
                    p.num(
                        rect.x + if live { 26.0 } else { 14.0 } * k,
                        rect.y + rect.h - 24.0 * k,
                        1.05 * k,
                        theme::faded(if active { theme::AMBER } else { theme::FAINT }, a * 1.5),
                        &meta,
                    );
                }
                Target::Go => go_button(
                    p,
                    rect,
                    k,
                    hot,
                    self.dev_ready().is_ok(),
                    self.tool.go(),
                    &self.dev_label(),
                ),
                _ => {}
            }
        }
    }

    /// What a map tile needs once there is a picture under it: shade behind
    /// its two lines of text, and a bar that says it is chosen — a tint over a
    /// photograph does not say so as plainly as it did over a flat card.
    fn dress_tile(
        &self,
        p: &mut Painter,
        rect: &Hit,
        k: f32,
        map: Option<usize>,
        active: bool,
        a: f32,
    ) {
        let pictured = map
            .and_then(|i| self.maps.get(i))
            .is_some_and(|m| self.art.contains(&m.name));
        if pictured {
            p.rect(rect.x, rect.y, rect.w, 46.0 * k, [0.0, 0.0, 0.0, 0.42 * a]);
            p.rect(
                rect.x,
                rect.y + rect.h - 30.0 * k,
                rect.w,
                30.0 * k,
                [0.0, 0.0, 0.0, 0.5 * a],
            );
        }
        if active {
            p.rect(
                rect.x,
                rect.y + rect.h - 3.0 * k,
                rect.w,
                3.0 * k,
                theme::faded(theme::ACCENT, a),
            );
            if self.picking() && map.is_some() {
                p.num_right(
                    rect.x + rect.w - 10.0 * k,
                    rect.y + rect.h - 22.0 * k,
                    1.0 * k,
                    theme::faded(theme::AMBER, a),
                    "ON",
                );
            }
        }
    }

    /// The lobby panel, under quick play's steppers.
    fn paint_lobby(&self, p: &mut Painter, hits: &[(Target, Hit)], k: f32) {
        let find = |want: Target| hits.iter().find(|(t, _)| *t == want).map(|(_, r)| *r);
        let left = MARGIN * k;
        if let (Some(lobby), Some(leave)) = (&self.lobby, find(Target::LobbyLeave)) {
            p.text(
                left,
                leave.y - 16.0 * k,
                1.05 * k,
                theme::FAINT,
                "LOBBY  /  SHARE THIS CODE",
            );
            p.num(left, leave.y + 3.0 * k, 2.3 * k, theme::AMBER, &lobby.code);
            button(p, &leave, k, "LEAVE", false, self.hovered(&leave));
            let mut y = leave.y + 42.0 * k;
            for (i, member) in lobby.members.iter().enumerate() {
                let a = self.arrive(i);
                p.rect(
                    left,
                    y - 3.0 * k,
                    COLUMN * k,
                    20.0 * k,
                    [1.0, 1.0, 1.0, 0.03 * a],
                );
                if member.leader {
                    p.rect(left, y - 3.0 * k, 2.0, 20.0 * k, theme::AMBER);
                }
                p.name(
                    left + 10.0 * k,
                    y,
                    1.2 * k,
                    theme::faded(
                        if member.you {
                            theme::TEXT
                        } else {
                            theme::TEXT_DIM
                        },
                        a,
                    ),
                    &member.name,
                );
                let tag = match (member.leader, member.you) {
                    (true, true) => "LEADER  /  YOU",
                    (true, false) => "LEADER",
                    (false, true) => "YOU",
                    _ => "",
                };
                p.num_right(
                    left + (COLUMN - 8.0) * k,
                    y + 1.0 * k,
                    0.95 * k,
                    theme::faded(
                        if member.leader {
                            theme::AMBER
                        } else {
                            theme::FAINT
                        },
                        a * 1.5,
                    ),
                    tag,
                );
                y += 22.0 * k;
            }
            return;
        }
        let (Some(create), Some(join)) = (find(Target::LobbyCreate), find(Target::LobbyJoin))
        else {
            return;
        };
        p.text(
            left,
            create.y - 16.0 * k,
            1.05 * k,
            theme::FAINT,
            "LOBBY  /  QUEUE WITH FRIENDS",
        );
        match &self.entering_code {
            Some(code) => {
                // One field across both buttons, in the callsign field's dress.
                let w = join.x + join.w - create.x;
                p.rect(create.x, create.y, w, create.h, [0.09, 0.11, 0.15, 1.0]);
                p.rect(create.x, create.y + create.h - 2.0, w, 2.0, theme::AMBER);
                p.num(
                    create.x + 10.0 * k,
                    create.y + 9.0 * k,
                    1.9 * k,
                    theme::AMBER,
                    code,
                );
                let caret = create.x + 12.0 * k + p.num_width(code, 1.9 * k);
                if (self.clock * 2.0) as i32 % 2 == 0 {
                    p.rect(caret, create.y + 8.0 * k, 2.0 * k, 20.0 * k, theme::AMBER);
                }
                p.text_right(
                    create.x + w - 10.0 * k,
                    create.y + 13.0 * k,
                    0.95 * k,
                    theme::TEXT_DIM,
                    if code.len() == LOBBY_CODE_LEN {
                        "ENTER JOINS"
                    } else {
                        "TYPE THE CODE"
                    },
                );
            }
            None => {
                button(p, &create, k, "CREATE", false, self.hovered(&create));
                button(p, &join, k, "JOIN BY CODE", false, self.hovered(&join));
            }
        }
    }

    fn paint_go(&self, p: &mut Painter, rect: &Hit, k: f32, hot: bool) {
        // In a lobby, only the leader's button does anything.
        if let (PlayKind::Quick, Some(lobby)) = (self.kind, &self.lobby) {
            if !lobby.leader {
                let sub = format!("WAITING FOR {}", lobby.leader_name().to_uppercase());
                return go_button(p, rect, k, hot, false, "IN THE LOBBY", &sub);
            }
        }
        let ready = match self.kind {
            PlayKind::Quick => !self.maps.is_empty(),
            PlayKind::Browse => self.rooms.get(self.room).is_some(),
            PlayKind::Practice => self.map_name().is_some(),
        };
        let label = match self.kind {
            PlayKind::Quick => "FIND MATCH",
            PlayKind::Browse => "JOIN ROOM",
            PlayKind::Practice => "START PRACTICE",
        };
        go_button(p, rect, k, hot, ready, label, &self.selection_label());
    }
}

/// The one big button, bottom right: what this screen does, and to what.
fn go_button(p: &mut Painter, rect: &Hit, k: f32, hot: bool, ready: bool, label: &str, sub: &str) {
    {
        let cut = 14.0 * k;
        let fill = if !ready {
            [1.0, 1.0, 1.0, 0.06]
        } else if hot {
            theme::with_alpha(theme::ACCENT, 1.0)
        } else {
            theme::with_alpha(theme::ACCENT, 0.86)
        };
        if ready {
            // A glow under it and out to the sides: the one lit thing here.
            let glow = theme::with_alpha(theme::ACCENT, if hot { 0.30 } else { 0.18 });
            let clear = theme::with_alpha(theme::ACCENT, 0.0);
            p.gradient_v(rect.x, rect.y + rect.h, rect.w, 14.0 * k, glow, clear);
        }
        p.rect(rect.x, rect.y, rect.w - cut, rect.h, fill);
        p.rect(rect.x + rect.w - cut, rect.y + cut, cut, rect.h - cut, fill);
        p.tri_aa(
            (rect.x + rect.w - cut, rect.y),
            (rect.x + rect.w, rect.y + cut),
            (rect.x + rect.w - cut, rect.y + cut),
            fill,
        );
        if ready {
            p.gradient_v(
                rect.x,
                rect.y + cut,
                rect.w,
                rect.h * 0.45,
                [1.0, 1.0, 1.0, 0.22],
                [1.0, 1.0, 1.0, 0.0],
            );
        } else {
            let edge = [1.0, 1.0, 1.0, 0.10];
            p.rect(rect.x, rect.y, rect.w - cut, 1.0, edge);
            p.rect(rect.x, rect.y + rect.h - 1.0, rect.w, 1.0, edge);
            p.rect(rect.x, rect.y, 1.0, rect.h, edge);
        }
        let ink = if ready {
            [0.03, 0.05, 0.09, 1.0]
        } else {
            theme::FAINT
        };
        // A chevron on the trailing side: this one goes somewhere. It leans
        // forward when the pointer is on it.
        let (cx, cy) = (
            rect.x + rect.w - 34.0 * k + if hot && ready { 4.0 * k } else { 0.0 },
            rect.y + rect.h * 0.5,
        );
        p.line(cx - 6.0 * k, cy - 11.0 * k, cx + 5.0 * k, cy, 3.0 * k, ink);
        p.line(cx + 5.0 * k, cy, cx - 6.0 * k, cy + 11.0 * k, 3.0 * k, ink);
        p.text(rect.x + 18.0 * k, rect.y + 13.0 * k, 2.4 * k, ink, label);
        p.num(
            rect.x + 18.0 * k,
            rect.y + rect.h - 20.0 * k,
            1.0 * k,
            if ready {
                [0.03, 0.05, 0.09, 0.8]
            } else {
                theme::FAINT
            },
            sub,
        );
    }
}

impl Front {
    fn paint_loadout(
        &self,
        p: &mut Painter,
        hits: &[(Target, Hit)],
        width: f32,
        height: f32,
        k: f32,
        top: f32,
    ) {
        let left = MARGIN * k;
        let right = width - MARGIN * k;
        if self.loadout.weapons.is_empty() {
            p.num(
                left,
                top + 20.0 * k,
                1.35 * k,
                theme::RED,
                "THE SERVER LISTED NO WEAPONS TO CHOOSE FROM",
            );
            return;
        }
        for (target, rect) in hits {
            let hot = self.hovered(rect);
            match target {
                Target::Side(side) => {
                    let active = *side == self.side;
                    p.rect(
                        rect.x,
                        rect.y,
                        rect.w,
                        rect.h,
                        if active {
                            theme::with_alpha(theme::ACCENT, 0.22)
                        } else if hot {
                            CARD_HOT
                        } else {
                            CARD
                        },
                    );
                    if active {
                        p.rect(rect.x, rect.y, rect.w, 2.0, theme::ACCENT);
                    }
                    let label = SIDE_LABELS[*side];
                    let w = p.text_width(label, 1.5 * k);
                    p.text(
                        rect.x + (rect.w - w) * 0.5,
                        rect.y + 14.0 * k,
                        1.5 * k,
                        if active { theme::TEXT } else { theme::TEXT_DIM },
                        label,
                    );
                }
                Target::Slot(slot) => {
                    let active = *slot == self.slot;
                    let a = self.arrive(*slot);
                    card(p, rect, 10.0 * k, active, hot, a);
                    p.text(
                        rect.x + 14.0 * k,
                        rect.y + 12.0 * k,
                        1.05 * k,
                        theme::faded(theme::FAINT, a * 1.6),
                        SLOT_LABELS[*slot],
                    );
                    let name = self.slot_name(*slot);
                    let w = p.text_width(&name, 1.9 * k);
                    p.text(
                        rect.x + (rect.w - w) * 0.5,
                        rect.y + 50.0 * k,
                        1.9 * k,
                        theme::faded(if active { theme::TEXT } else { theme::TEXT_DIM }, a),
                        &name,
                    );
                    let (at, of) = self.slot_position(*slot);
                    let count = format!("{at} OF {of}");
                    p.num_right(
                        rect.x + rect.w - 14.0 * k,
                        rect.y + 12.0 * k,
                        1.0 * k,
                        theme::faded(theme::FAINT, a * 1.6),
                        &count,
                    );
                }
                Target::Step(_, step) => arrow(p, rect, k, *step, hot),
                _ => {}
            }
        }

        // ---- the detail -----------------------------------------------------
        let x = left + (COLUMN + 32.0) * k;
        let w = right - x;
        let h = height - top - (MARGIN + 34.0) * k;
        let cut = 22.0 * k;
        let fill = [0.016, 0.021, 0.030, 0.92];
        p.rect(x, top + cut, w, h - cut, fill);
        p.rect(x, top, w - cut, cut, fill);
        p.tri_aa(
            (x + w - cut, top),
            (x + w, top + cut),
            (x + w - cut, top + cut),
            fill,
        );
        p.grid(x, top, w - cut, h, 24.0 * k, theme::GRID);
        p.rect(x, top, w - cut, 2.0, theme::ACCENT);

        let pad = 28.0 * k;
        let grow = smooth((self.clock - self.detail_since) / 0.28);
        p.text(
            x + pad,
            top + pad,
            1.05 * k,
            theme::FAINT,
            &format!("{}  /  {}", SIDE_LABELS[self.side], SLOT_LABELS[self.slot]),
        );
        p.text(
            x + pad,
            top + pad + 22.0 * k,
            3.6 * k,
            theme::TEXT,
            &self.slot_name(self.slot),
        );

        // The picture has the top right to itself; everything that has to be
        // read sits to its left or below it, never under it.
        let art_w = (w * 0.42).min(400.0 * k);
        let art_h = art_w * 0.4;
        let art_x = x + w - pad - art_w;
        let art_y = top + pad;
        let ink = theme::with_alpha(theme::ACCENT, 0.45 + 0.4 * grow);
        let body_y = art_y + art_h.max(110.0 * k) + 26.0 * k;

        if self.slot == 2 {
            operator_art(p, art_x + art_w * 0.5, art_y, art_h.max(110.0 * k), ink);
            self.paint_operator(p, x + pad, top + pad + 70.0 * k, k);
        } else if let Some(spec) = self.picked_spec(self.slot) {
            let class = if spec.model.is_empty() {
                &spec.id
            } else {
                &spec.model
            };
            weapon_art(p, art_x, art_y, art_w, art_h, weapon_class(class), ink);

            // Tags: how it fires, and who may carry it.
            let mut tx = x + pad;
            for text in [
                if spec.bolt_action {
                    "BOLT ACTION"
                } else if spec.auto {
                    "AUTOMATIC"
                } else {
                    "SEMI-AUTOMATIC"
                },
                match spec.side.as_str() {
                    "t" => "T SIDE ONLY",
                    "ct" => "CT SIDE ONLY",
                    _ => "BOTH SIDES",
                },
                if spec.zoom_levels.is_empty() {
                    "IRON SIGHTS"
                } else {
                    "SCOPED"
                },
            ] {
                tx += tag(p, tx, top + pad + 70.0 * k, k, text) + 8.0 * k;
            }

            // The numbers, each against the best of what is on offer.
            let label_w = 150.0 * k;
            let value_w = 120.0 * k;
            let bar_x = x + pad + label_w;
            let bar_w = w - pad * 2.0 - label_w - value_w;
            for (i, stat) in self.stats(spec).iter().enumerate() {
                let y = body_y + i as f32 * 30.0 * k;
                p.text(x + pad, y + 2.0 * k, 1.25 * k, theme::TEXT_DIM, stat.label);
                p.rect(bar_x, y + 4.0 * k, bar_w, 8.0 * k, [1.0, 1.0, 1.0, 0.07]);
                // The bar and its figure grow together, so the number on screen
                // is always the length of the bar beside it.
                p.rect(
                    bar_x,
                    y + 4.0 * k,
                    bar_w * (stat.share * grow).clamp(0.0, 1.0),
                    8.0 * k,
                    theme::ACCENT,
                );
                p.num_right(
                    x + w - pad,
                    y,
                    1.5 * k,
                    theme::TEXT,
                    &stat.format(stat.value * grow),
                );
            }
        }

        // ---- the kit --------------------------------------------------------
        //
        // Everything this side carries, in one row: the answer to "what do I
        // spawn with", which the slot being edited above is only a third of.
        let kit_h = 78.0 * k;
        let kit_y = top + h - pad * 0.6 - kit_h;
        p.rule(
            x + pad,
            kit_y - 14.0 * k,
            w - pad * 2.0,
            theme::FAINT,
            theme::ACCENT,
        );
        let cells = self.kit();
        let cell_w = (w - pad * 2.0) / cells.len() as f32;
        for (i, (label, name, class, slot)) in cells.iter().enumerate() {
            let cx = x + pad + i as f32 * cell_w;
            let current = *slot == Some(self.slot);
            p.text(cx, kit_y, 1.0 * k, theme::FAINT, label);
            let ink = if current {
                theme::with_alpha(theme::ACCENT, 0.9)
            } else {
                theme::with_alpha(theme::TEXT_DIM, 0.45)
            };
            let (aw, ah) = (cell_w.min(150.0 * k) * 0.8, 34.0 * k);
            match class {
                Some(class) => {
                    weapon_art(p, cx, kit_y + 16.0 * k, aw.min(ah * 2.5), ah, *class, ink)
                }
                None => operator_art(p, cx + ah * 0.42, kit_y + 16.0 * k, ah, ink),
            }
            p.text(
                cx,
                kit_y + 58.0 * k,
                1.25 * k,
                if current {
                    theme::TEXT
                } else {
                    theme::TEXT_DIM
                },
                name,
            );
        }
    }

    fn paint_operator(&self, p: &mut Painter, x: f32, y: f32, k: f32) {
        let Some(agent) = self.loadout.agent(self.side) else {
            p.num(
                x,
                y,
                1.35 * k,
                theme::TEXT_DIM,
                "THE SERVER LISTED NO OPERATORS FOR THIS SIDE",
            );
            return;
        };
        let mut tx = x;
        for side in &agent.sides {
            let text = if side == "t" { "T SIDE" } else { "CT SIDE" };
            tx += tag(p, tx, y, k, text) + 8.0 * k;
        }
        p.num(
            x,
            y + 44.0 * k,
            1.2 * k,
            theme::TEXT_DIM,
            "WORN IN EVERY MATCH ON THIS SIDE",
        );
        p.num(
            x,
            y + 64.0 * k,
            1.2 * k,
            theme::FAINT,
            "THE MODEL ITSELF IS DRAWN IN THE MATCH",
        );
    }

    /// What this side carries: label, name, the picture's class (`None` is the
    /// operator), and which editable slot it is, if any.
    fn kit(&self) -> Vec<(&'static str, String, Option<WeaponClass>, Option<usize>)> {
        let class_of = |slot: usize| {
            self.picked_spec(slot).map(|spec| {
                weapon_class(if spec.model.is_empty() {
                    &spec.id
                } else {
                    &spec.model
                })
            })
        };
        let mut out = vec![
            (
                "PRIMARY",
                self.slot_name(0),
                Some(class_of(0).unwrap_or(WeaponClass::Rifle)),
                Some(0),
            ),
            (
                "SECONDARY",
                self.slot_name(1),
                Some(class_of(1).unwrap_or(WeaponClass::Pistol)),
                Some(1),
            ),
        ];
        // The blade is not a choice, so it has no slot — but it is carried, and
        // a kit that left it out would be describing less than the spawn.
        if let Some(melee) = self.weapons.iter().find(|w| w.slot == "melee") {
            out.push((
                "MELEE",
                melee.name.to_uppercase(),
                Some(WeaponClass::Knife),
                None,
            ));
        }
        out.push(("OPERATOR", self.slot_name(2), None, Some(2)));
        out
    }

    fn slot_name(&self, slot: usize) -> String {
        if slot < 2 {
            self.loadout
                .picked(self.side, slot)
                .map(|w| w.name.to_uppercase())
                .unwrap_or_else(|| "NOTHING OFFERED".into())
        } else {
            self.loadout
                .agent(self.side)
                .map(|a| a.name.to_uppercase())
                .unwrap_or_else(|| "NONE OFFERED".into())
        }
    }

    /// Which of how many: `(2, 5)` for the second of five on offer.
    fn slot_position(&self, slot: usize) -> (usize, usize) {
        if slot < 2 {
            let offered = self.loadout.offered(self.side, slot);
            let picked = self.loadout.picked(self.side, slot).map(|w| w.id.as_str());
            let at = offered
                .iter()
                .position(|w| Some(w.id.as_str()) == picked)
                .unwrap_or(0);
            (at + usize::from(!offered.is_empty()), offered.len())
        } else {
            let offered = self.loadout.agents_for(self.side);
            let picked = self.loadout.agent(self.side).map(|a| a.id.as_str());
            let at = offered
                .iter()
                .position(|a| Some(a.id.as_str()) == picked)
                .unwrap_or(0);
            (at + usize::from(!offered.is_empty()), offered.len())
        }
    }

    fn picked_spec(&self, slot: usize) -> Option<&WeaponSpec> {
        let id = &self.loadout.picked(self.side, slot)?.id;
        self.weapons.iter().find(|w| &w.id == id)
    }

    /// One weapon's numbers, each with its share of the best on the shelf.
    ///
    /// Relative to what is *offered*, not to an absolute scale: the question
    /// this screen answers is "which of these", and a bar measured against a
    /// knife's range or a made-up maximum answers a different one.
    fn stats(&self, spec: &WeaponSpec) -> Vec<Stat> {
        let guns: Vec<&WeaponSpec> = self
            .weapons
            .iter()
            .filter(|w| w.slot == "primary" || w.slot == "secondary")
            .collect();
        let best = |f: &dyn Fn(&WeaponSpec) -> f32| {
            guns.iter()
                .map(|w| f(w))
                .fold(0.0f32, f32::max)
                .max(f32::EPSILON)
        };
        let row = |label: &'static str, unit: StatUnit, f: &dyn Fn(&WeaponSpec) -> f32| Stat {
            label,
            value: f(spec),
            share: f(spec) / best(f),
            unit,
        };
        vec![
            row("DAMAGE", StatUnit::Whole, &|w| {
                w.damage * w.pellets.max(1) as f32
            }),
            row("FIRE RATE", StatUnit::Rpm, &|w| w.rpm),
            row("MAGAZINE", StatUnit::Whole, &|w| w.mag as f32),
            row("RANGE", StatUnit::Whole, &|w| w.range),
            row("HEADSHOT", StatUnit::Times, &|w| w.head_multiplier),
            row("RELOAD", StatUnit::Seconds, &|w| w.reload_time),
            row("RECOIL", StatUnit::Tenths, &|w| w.kickback),
        ]
    }

    fn paint_search(
        &self,
        p: &mut Painter,
        hits: &[(Target, Hit)],
        width: f32,
        height: f32,
        k: f32,
    ) {
        p.rect(0.0, 0.0, width, height, theme::SCRIM);
        let panel = search_panel(width, height, k);
        let cut = 18.0 * k;
        let fill = theme::panel(0.98);
        p.rect(panel.x, panel.y + cut, panel.w, panel.h - cut, fill);
        p.rect(panel.x, panel.y, panel.w - cut, cut, fill);
        p.tri_aa(
            (panel.x + panel.w - cut, panel.y),
            (panel.x + panel.w, panel.y + cut),
            (panel.x + panel.w - cut, panel.y + cut),
            fill,
        );
        p.grid(
            panel.x,
            panel.y,
            panel.w - cut,
            panel.h,
            20.0 * k,
            theme::GRID,
        );
        p.rect(panel.x, panel.y, panel.w - cut, 2.0, theme::ACCENT);

        let pad = 28.0 * k;
        let elapsed = (self.clock - self.search.started).max(0.0);
        // Three different answers, and the title says which: a room somebody
        // is in is a match found; nothing open is a room about to be opened.
        let title = match &self.search.found {
            Some(Found::Room(_)) => "MATCH FOUND",
            Some(Found::Open { .. }) => "OPENING A ROOM",
            None => "FINDING A MATCH",
        };
        p.text(panel.x + pad, panel.y + pad, 2.6 * k, theme::TEXT, title);
        let stamp = format!("{:02}:{:02}", (elapsed / 60.0) as u32, elapsed as u32 % 60);
        p.num_right(
            panel.x + panel.w - pad,
            panel.y + pad,
            2.4 * k,
            theme::AMBER,
            &stamp,
        );
        p.num(
            panel.x + pad,
            panel.y + pad + 34.0 * k,
            1.15 * k,
            theme::TEXT_DIM,
            &self.search.asked,
        );

        // A sweep while the answer is out, a full bar once it is in.
        let track = Hit {
            x: panel.x + pad,
            y: panel.y + pad + 66.0 * k,
            w: panel.w - pad * 2.0,
            h: 4.0 * k,
        };
        p.rect(track.x, track.y, track.w, track.h, [1.0, 1.0, 1.0, 0.07]);
        if self.search.found.is_some() {
            p.rect(track.x, track.y, track.w, track.h, theme::GREEN);
        } else {
            let span = track.w * 0.22;
            let at = (elapsed * 0.7).fract() * (track.w + span) - span;
            let x0 = at.max(0.0);
            let x1 = (at + span).min(track.w);
            p.rect(
                track.x + x0,
                track.y,
                (x1 - x0).max(0.0),
                track.h,
                theme::ACCENT,
            );
        }
        p.num(
            panel.x + pad,
            panel.y + pad + 86.0 * k,
            1.35 * k,
            if self.search.found.is_some() {
                theme::GREEN
            } else {
                theme::TEXT
            },
            &self.search.status,
        );
        for (target, rect) in hits {
            if *target == Target::Cancel {
                button(p, rect, k, "CANCEL", false, self.hovered(rect));
            }
        }
    }

    fn paint_loading(&self, p: &mut Painter, width: f32, height: f32, k: f32) {
        // The map itself, as its floor plan, once there is one: the right half
        // of the screen, faint, behind everything.
        let load = &self.loading;
        if !load.plan.is_empty() && load.plan.len() <= PLAN_LIMIT && load.plan_size > 0.0 {
            let side = (height * 0.78).min(width * 0.5);
            let cell = side / load.plan_size;
            let (ox, oy) = (width - side - MARGIN * k, (height - side) * 0.5);
            let a = smooth((self.clock - self.since) / 0.6);
            for run in &load.plan {
                // Higher floors a little brighter, as the radar draws them.
                let shade = 0.05 + 0.03 * run.band as f32;
                p.rect(
                    ox + run.x0 * cell,
                    oy + run.y * cell,
                    (run.x1 - run.x0) * cell,
                    cell.max(1.0),
                    theme::with_alpha(theme::ACCENT, (shade * a).min(0.2)),
                );
            }
        }

        let x = MARGIN * k;
        p.text(x, 70.0 * k, 1.2 * k, theme::FAINT, "LOADING");
        p.text(x, 96.0 * k, 6.4 * k, theme::TEXT, &map_title(&load.map));
        p.rect(x, 154.0 * k, 96.0 * k, 2.0, theme::ACCENT);
        let mut line = String::new();
        for part in [&load.mode, &load.room] {
            if !part.is_empty() {
                if !line.is_empty() {
                    line.push_str("  /  ");
                }
                line.push_str(part);
            }
        }
        p.num(x, 172.0 * k, 1.4 * k, theme::TEXT_DIM, &line);

        steps(p, &self.steps, x, 232.0 * k, k, self.clock);

        let tip = TIPS[load.map.bytes().map(usize::from).sum::<usize>() % TIPS.len()];
        p.text(
            x,
            height - 132.0 * k,
            1.0 * k,
            theme::FAINT,
            "WORTH KNOWING",
        );
        p.text(x, height - 112.0 * k, 1.3 * k, theme::TEXT_DIM, tip);

        let progress = self.progress();
        bar(p, x, height - 70.0 * k, width - x * 2.0, 4.0 * k, progress);
        p.num_right(
            width - x,
            height - 100.0 * k,
            1.8 * k,
            theme::TEXT,
            &format!("{:.0}%", progress * 100.0),
        );
    }
}

// -----------------------------------------------------------------------------
// Layout constants, in 720p pixels. `unit` scales them to the window.
// -----------------------------------------------------------------------------

const WORDMARK: &str = "HASSAULT";
/// The key hints along the foot of the menu.
const HINT_PLAY: &str =
    "ENTER PLAYS   /   ARROWS CHOOSE   /   1 2 3 TABS   /   THE KEY ABOVE TAB OPENS THE CONSOLE IN A MATCH";
/// How much of the server view's width the room list takes; the roster
/// has the rest.
const SERVER_SPLIT: f32 = 0.56;
const HINT_DEV: &str =
    "ENTER STARTS   /   UP DOWN TOOL   /   LEFT RIGHT MAP   /   [ ] DRILL   /   TAB PLAY";
const HINT_LOADOUT: &str = "ARROWS CHOOSE AND CHANGE   /   SPACE SWAPS SIDE   /   TAB PLAY";
const MARGIN: f32 = 40.0;
/// The top bar's height.
const TOP: f32 = 64.0;
/// The left column's width.
const COLUMN: f32 = 300.0;
const GO_W: f32 = 320.0;
const GO_H: f32 = 68.0;

/// One 720p pixel, in window pixels. Not rounded: the menu is laid out in
/// floats and nothing in it has to land on the HUD's integer grid.
pub fn unit(height: f32) -> f32 {
    (height / 720.0).max(0.6)
}

fn button_at(x: f32, y: f32, w: f32, k: f32) -> Hit {
    Hit {
        x,
        y,
        w: w * k,
        h: 44.0 * k,
    }
}

fn search_panel(width: f32, height: f32, k: f32) -> Hit {
    let w = (620.0 * k).min(width - MARGIN * k);
    let h = 216.0 * k;
    Hit {
        x: (width - w) * 0.5,
        y: (height - h) * 0.38,
        w,
        h,
    }
}

// -----------------------------------------------------------------------------
// Shapes every screen is built from
// -----------------------------------------------------------------------------

/// The whole window, before anything is drawn on it.
///
/// Dark, with the faint grid the rest of the UI carries and one slab cut
/// across the right: a field that is neither a flat fill nor a gradient.
fn backdrop(p: &mut Painter, width: f32, height: f32, k: f32, pictured: bool) {
    // Linear values, like every colour here: the surface is sRGB, so these
    // read as a near-black blue rather than the mid grey their numbers suggest.
    //
    // With a picture behind the menu the base is left off — it is the picture
    // — and the slab becomes a wash over it, so the lean still divides the
    // screen without hiding half the map.
    if !pictured {
        let base = [0.0075, 0.0105, 0.0155, 1.0];
        p.rect(0.0, 0.0, width, height, base);
        // Lit from the top: the field under the bar is a shade bluer than the
        // foot, so the screen has a direction instead of being one flat fill.
        p.gradient_v(0.0, 0.0, width, height * 0.62, [0.0190, 0.0290, 0.0500, 1.0], base);
    }
    let lean = height * 0.42;
    let x = width * 0.58;
    let slab = if pictured {
        [0.0, 0.004, 0.012, 0.34]
    } else {
        [0.0125, 0.0165, 0.0240, 1.0]
    };
    p.rect(x + lean, 0.0, width - x - lean, height, slab);
    p.tri((x, height), (x + lean, 0.0), (x + lean, height), slab);
    p.grid(
        0.0,
        0.0,
        width,
        height,
        36.0 * k,
        theme::with_alpha(theme::GRID, 0.022),
    );
    p.line(
        x,
        height,
        x + lean,
        0.0,
        1.0,
        theme::with_alpha(theme::ACCENT, 0.16),
    );
    // And darkened towards the foot, where the hints and the button sit: the
    // button is the brightest thing on the screen, and it reads as such
    // against a floor that falls away under it.
    p.gradient_v(
        0.0,
        height * 0.66,
        width,
        height * 0.34,
        [0.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, if pictured { 0.5 } else { 0.38 }],
    );
}

/// A resting block's fill: the accent's hue at almost no strength, so a card
/// reads as a lift in the surface rather than a grey box laid on it.
const CARD: [f32; 4] = [0.45, 0.58, 0.85, 0.045];
const CARD_HOT: [f32; 4] = [0.45, 0.58, 0.85, 0.085];

/// A small tracked label over a column.
fn heading(p: &mut Painter, x: f32, y: f32, k: f32, text: &str) {
    p.text(x, y, 1.05 * k, theme::FAINT, text);
    p.rect(x, y + 13.0 * k, 28.0 * k, 2.0, theme::ACCENT);
}

/// A selectable block: cut top-right corner, outlined, lit from the top, with
/// an accent edge and a bar down its leading side when chosen.
fn card(p: &mut Painter, r: &Hit, cut: f32, active: bool, hot: bool, a: f32) {
    // Slides in from the left a little as it fades up.
    let x = r.x - (1.0 - a) * 10.0;
    let fill = if active {
        theme::with_alpha(theme::ACCENT, 0.2 * a)
    } else if hot {
        theme::faded(CARD_HOT, a)
    } else {
        theme::faded(CARD, a)
    };
    p.rect(x, r.y + cut, r.w, r.h - cut, fill);
    p.rect(x, r.y, r.w - cut, cut, fill);
    p.tri_aa(
        (x + r.w - cut, r.y),
        (x + r.w, r.y + cut),
        (x + r.w - cut, r.y + cut),
        fill,
    );
    // A sheen over the top half: the block is a surface, not a tint.
    let sheen = if active { 0.10 } else if hot { 0.07 } else { 0.035 };
    let lit = [1.0, 1.0, 1.0, sheen * a];
    p.rect(x, r.y, r.w - cut, cut, lit);
    p.tri_aa(
        (x + r.w - cut, r.y),
        (x + r.w, r.y + cut),
        (x + r.w - cut, r.y + cut),
        lit,
    );
    p.gradient_v(x, r.y + cut, r.w, (r.h - cut) * 0.55, lit, [1.0, 1.0, 1.0, 0.0]);
    // The outline, following the cut. One pixel: it defines the edge against
    // a picture or the grid without becoming a frame.
    let edge = if active {
        theme::with_alpha(theme::ACCENT, 0.75 * a)
    } else if hot {
        [1.0, 1.0, 1.0, 0.22 * a]
    } else {
        [1.0, 1.0, 1.0, 0.085 * a]
    };
    p.rect(x, r.y, r.w - cut, 1.0, edge);
    p.rect(x, r.y + r.h - 1.0, r.w, 1.0, edge);
    p.rect(x, r.y, 1.0, r.h, edge);
    p.rect(x + r.w - 1.0, r.y + cut, 1.0, r.h - cut, edge);
    p.line(x + r.w - cut, r.y, x + r.w, r.y + cut, 1.0, edge);
    if active {
        p.rect(x, r.y, r.w - cut, 2.0, theme::faded(theme::ACCENT, a));
        p.rect(x, r.y, 3.0, r.h, theme::faded(theme::ACCENT, a));
    }
}

/// A plain button. `primary` is the one the screen wants pressed.
fn button(p: &mut Painter, r: &Hit, k: f32, label: &str, primary: bool, hot: bool) {
    let fill = match (primary, hot) {
        (true, true) => theme::with_alpha(theme::ACCENT, 1.0),
        (true, false) => theme::with_alpha(theme::ACCENT, 0.85),
        (false, true) => [1.0, 1.0, 1.0, 0.12],
        (false, false) => [1.0, 1.0, 1.0, 0.06],
    };
    p.rect(r.x, r.y, r.w, r.h, fill);
    p.gradient_v(
        r.x,
        r.y,
        r.w,
        r.h * 0.5,
        [1.0, 1.0, 1.0, if primary { 0.16 } else { 0.05 }],
        [1.0, 1.0, 1.0, 0.0],
    );
    if !primary {
        let edge = [1.0, 1.0, 1.0, if hot { 0.28 } else { 0.13 }];
        p.rect(r.x, r.y, r.w, 1.0, edge);
        p.rect(r.x, r.y + r.h - 1.0, r.w, 1.0, edge);
        p.rect(r.x, r.y, 1.0, r.h, edge);
        p.rect(r.x + r.w - 1.0, r.y, 1.0, r.h, edge);
    }
    let scale = 1.4 * k;
    let w = p.text_width(label, scale);
    p.text(
        r.x + (r.w - w) * 0.5,
        r.y + (r.h - 7.0 * scale) * 0.5,
        scale,
        if primary {
            [0.03, 0.05, 0.09, 1.0]
        } else {
            theme::TEXT
        },
        label,
    );
}

/// A stepper's arrow: a chevron, drawn rather than typed.
fn arrow(p: &mut Painter, r: &Hit, k: f32, step: i32, hot: bool) {
    p.rect(
        r.x,
        r.y,
        r.w,
        r.h,
        if hot {
            [1.0, 1.0, 1.0, 0.13]
        } else {
            [1.0, 1.0, 1.0, 0.06]
        },
    );
    let (cx, cy) = (r.x + r.w * 0.5, r.y + r.h * 0.5);
    let d = 6.0 * k * step.signum() as f32;
    let ink = if hot { theme::TEXT } else { theme::TEXT_DIM };
    p.line(cx - d * 0.5, cy - 7.0 * k, cx + d * 0.5, cy, 2.0 * k, ink);
    p.line(cx + d * 0.5, cy, cx - d * 0.5, cy + 7.0 * k, 2.0 * k, ink);
}

/// A small outlined label. Returns its width.
fn tag(p: &mut Painter, x: f32, y: f32, k: f32, text: &str) -> f32 {
    let scale = 1.0 * k;
    let w = p.text_width(text, scale) + 16.0 * k;
    let h = 20.0 * k;
    p.rect(x, y, w, h, [1.0, 1.0, 1.0, 0.05]);
    p.rect(x, y, w, 1.0, theme::FAINT);
    p.rect(x, y + h - 1.0, w, 1.0, theme::FAINT);
    p.text(
        x + 8.0 * k,
        y + (h - 7.0 * scale) * 0.5,
        scale,
        theme::TEXT_DIM,
        text,
    );
    w
}

/// A progress bar: a trough and how much of it is behind us.
fn bar(p: &mut Painter, x: f32, y: f32, w: f32, h: f32, progress: f32) {
    p.rect(x, y, w, h, [1.0, 1.0, 1.0, 0.07]);
    p.rect(x, y, w * progress.clamp(0.0, 1.0), h, theme::ACCENT);
}

/// The phase list of a boot or a load, one line each.
fn steps(p: &mut Painter, steps: &[Step], x: f32, y: f32, k: f32, clock: f32) {
    for (i, step) in steps.iter().enumerate() {
        let y = y + i as f32 * 28.0 * k;
        let (mark, ink) = match step.state {
            StepState::Waiting => ("    ", theme::FAINT),
            // Three dots that walk: a phase that is running should look it.
            StepState::Running => (
                [".   ", "..  ", "... "][(clock * 3.0) as usize % 3],
                theme::AMBER,
            ),
            StepState::Done => ("OK  ", theme::GREEN),
            StepState::Skipped => ("SKIP", theme::TEXT_DIM),
            StepState::Failed => ("FAIL", theme::RED),
        };
        p.num(x, y, 1.3 * k, ink, mark);
        let label_ink = match step.state {
            StepState::Waiting => theme::FAINT,
            StepState::Failed => theme::RED,
            _ => theme::TEXT,
        };
        p.text(x + 64.0 * k, y, 1.35 * k, label_ink, &step.label);
        if !step.note.is_empty() {
            p.num(x + 330.0 * k, y, 1.2 * k, theme::TEXT_DIM, &step.note);
        }
    }
}

/// The six shapes a weapon is drawn as.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WeaponClass {
    Rifle,
    Smg,
    Sniper,
    Shotgun,
    Pistol,
    Knife,
}

/// Which shape a served model id is drawn as.
///
/// By what the id *contains*, so a new rifle variant draws as a rifle without
/// this list hearing about it. Anything unrecognised is a rifle, which is what
/// most of the roster is.
pub fn weapon_class(model: &str) -> WeaponClass {
    let m = model.to_ascii_lowercase();
    if m.contains("sniper") || m.contains("scout") {
        WeaponClass::Sniper
    } else if m.contains("shotgun") || m.contains("breacher") {
        WeaponClass::Shotgun
    } else if m.contains("pistol") || m.contains("cannon") {
        WeaponClass::Pistol
    } else if m.contains("knife") {
        WeaponClass::Knife
    } else if m.contains("smg") {
        WeaponClass::Smg
    } else {
        WeaponClass::Rifle
    }
}

/// A weapon in profile, muzzle to the right, fitted into `w` by `h`.
///
/// Drawn on a 100 by 40 grid out of rectangles and four-cornered plates — the
/// painter has no curves and no images, and a profile made of the parts a
/// weapon is actually made of (stock, receiver, magazine, barrel) reads as one
/// at any size. The HUD's ammo block has a smaller, coarser set for 36 pixels;
/// these are for when the weapon is the subject.
pub fn weapon_art(
    p: &mut Painter,
    x: f32,
    y: f32,
    w: f32,
    h: f32,
    class: WeaponClass,
    ink: [f32; 4],
) {
    // One scale for both axes, so a weapon is never stretched to fill its box;
    // centred in whatever is left over.
    let s = (w / 100.0).min(h / 40.0);
    let (ox, oy) = (x + (w - 100.0 * s) * 0.5, y + (h - 40.0 * s) * 0.5);
    let mut r = |gx: f32, gy: f32, gw: f32, gh: f32| {
        p.rect(ox + gx * s, oy + gy * s, gw * s, gh * s, ink);
    };
    match class {
        WeaponClass::Rifle => {
            r(18.0, 9.0, 30.0, 10.0);
            r(48.0, 10.0, 26.0, 8.0);
            r(74.0, 12.0, 21.0, 3.0);
            r(94.0, 11.0, 5.0, 5.0);
            r(84.0, 7.0, 2.0, 5.0);
            r(24.0, 6.0, 12.0, 3.0);
        }
        WeaponClass::Smg => {
            r(2.0, 11.0, 16.0, 2.0);
            r(2.0, 11.0, 2.0, 10.0);
            r(18.0, 9.0, 34.0, 11.0);
            r(52.0, 12.0, 18.0, 3.0);
            r(69.0, 11.0, 4.0, 5.0);
            r(40.0, 20.0, 6.0, 17.0);
            r(56.0, 15.0, 5.0, 10.0);
            r(26.0, 6.0, 10.0, 3.0);
        }
        WeaponClass::Sniper => {
            r(20.0, 11.0, 26.0, 8.0);
            r(46.0, 13.0, 49.0, 3.0);
            r(94.0, 12.0, 6.0, 5.0);
            r(38.0, 19.0, 7.0, 6.0);
            r(24.0, 3.0, 28.0, 5.0);
            r(28.0, 8.0, 3.0, 3.0);
            r(44.0, 8.0, 3.0, 3.0);
        }
        WeaponClass::Shotgun => {
            r(20.0, 10.0, 24.0, 9.0);
            r(44.0, 10.0, 54.0, 4.0);
            r(44.0, 15.0, 42.0, 3.0);
            r(54.0, 14.0, 18.0, 7.0);
        }
        WeaponClass::Pistol => {
            r(26.0, 8.0, 46.0, 9.0);
            r(72.0, 10.0, 4.0, 4.0);
            r(30.0, 17.0, 30.0, 4.0);
            r(46.0, 21.0, 10.0, 2.0);
            r(54.0, 21.0, 2.0, 5.0);
        }
        WeaponClass::Knife => {
            r(36.0, 11.0, 4.0, 16.0);
            r(10.0, 15.0, 26.0, 8.0);
        }
    }
    // The plates that are not axis-aligned: stocks, grips, magazines, blades.
    let plates: &[[(f32, f32); 4]] = match class {
        WeaponClass::Rifle => &[
            [(0.0, 12.0), (18.0, 10.0), (18.0, 19.0), (0.0, 23.0)],
            [(26.0, 19.0), (33.0, 19.0), (30.0, 31.0), (24.0, 31.0)],
            [(38.0, 19.0), (46.0, 19.0), (50.0, 34.0), (43.0, 34.0)],
        ],
        WeaponClass::Smg => &[[(26.0, 20.0), (33.0, 20.0), (30.0, 32.0), (24.0, 32.0)]],
        WeaponClass::Sniper => &[
            [(0.0, 13.0), (20.0, 11.0), (20.0, 19.0), (0.0, 25.0)],
            [(27.0, 19.0), (33.0, 19.0), (31.0, 30.0), (26.0, 30.0)],
            [(62.0, 16.0), (64.0, 16.0), (60.0, 32.0), (58.0, 32.0)],
            [(66.0, 16.0), (68.0, 16.0), (72.0, 32.0), (70.0, 32.0)],
        ],
        WeaponClass::Shotgun => &[
            [(0.0, 13.0), (20.0, 10.0), (20.0, 19.0), (0.0, 24.0)],
            [(26.0, 19.0), (32.0, 19.0), (30.0, 29.0), (25.0, 29.0)],
        ],
        WeaponClass::Pistol => &[[(30.0, 17.0), (44.0, 17.0), (40.0, 36.0), (26.0, 36.0)]],
        WeaponClass::Knife => &[[(40.0, 14.0), (86.0, 15.0), (96.0, 21.0), (40.0, 24.0)]],
    };
    for plate in plates {
        let at = |i: usize| (ox + plate[i].0 * s, oy + plate[i].1 * s);
        p.tri(at(0), at(1), at(2), ink);
        p.tri(at(0), at(2), at(3), ink);
    }
}

/// A figure, not a portrait: head and shoulders, `h` tall, centred on `cx`.
pub fn operator_art(p: &mut Painter, cx: f32, y: f32, h: f32, ink: [f32; 4]) {
    let head = h * 0.2;
    p.disc(cx, y + head, head, 28, ink);
    let (top, bottom) = (y + h * 0.46, y + h);
    let (neck, shoulder) = (h * 0.2, h * 0.42);
    p.rect(cx - neck, top, neck * 2.0, bottom - top, ink);
    p.tri(
        (cx - shoulder, bottom),
        (cx - neck, top),
        (cx - neck, bottom),
        ink,
    );
    p.tri(
        (cx + shoulder, bottom),
        (cx + neck, top),
        (cx + neck, bottom),
        ink,
    );
}

#[derive(Debug, Clone, Copy, PartialEq)]
enum StatUnit {
    Whole,
    Rpm,
    Times,
    Seconds,
    Tenths,
}

struct Stat {
    label: &'static str,
    value: f32,
    /// This weapon's value over the best on offer, 0 to 1.
    share: f32,
    unit: StatUnit,
}

impl Stat {
    fn format(&self, value: f32) -> String {
        match self.unit {
            StatUnit::Whole => format!("{value:.0}"),
            StatUnit::Rpm => format!("{value:.0} RPM"),
            StatUnit::Times => format!("x{value:.1}"),
            StatUnit::Seconds => format!("{value:.1} S"),
            StatUnit::Tenths => format!("{value:.1}"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::menu::{LoadoutAgent, LoadoutWeapon};

    fn room(id: &str, map: &str, mode: &str, players: u32) -> RoomEntry {
        RoomEntry {
            id: id.into(),
            map: map.into(),
            players,
            seats: 10,
            mode: mode.into(),
        }
    }

    fn front() -> Front {
        let mut f = Front::new("games.example", "Operative-7");
        f.maps = ["hd_aim", "hd_assault", "hd_dust2"]
            .iter()
            .map(|n| MapEntry {
                name: n.to_string(),
                size: 50_000,
            })
            .collect();
        f.modes = [("dm", "Deathmatch"), ("tdm", "Team Deathmatch")]
            .iter()
            .map(|(id, name)| ModeEntry {
                id: id.to_string(),
                name: name.to_string(),
            })
            .collect();
        for (id, slot, side) in [
            ("assault", "primary", "any"),
            ("raider", "primary", "t"),
            ("pistol", "secondary", "any"),
        ] {
            f.loadout.weapons.push(LoadoutWeapon {
                id: id.into(),
                name: id.to_uppercase(),
                slot: slot.into(),
                side: side.into(),
            });
            f.weapons.push(WeaponSpec {
                id: id.into(),
                name: id.to_uppercase(),
                slot: slot.into(),
                side: side.into(),
                damage: 20.0,
                rpm: 600.0,
                mag: 30,
                range: 100.0,
                pellets: 1,
                ..Default::default()
            });
        }
        f.loadout.agents.push(LoadoutAgent {
            id: "ghost".into(),
            name: "Ghost".into(),
            sides: vec!["t".into(), "ct".into()],
        });
        f.show(Screen::Home);
        f.clock = 10.0;
        f
    }

    fn click(f: &mut Front, target: Target) -> Option<Intent> {
        let hit = f
            .hits(None, 1280.0, 720.0)
            .into_iter()
            .find(|(t, _)| *t == target)
            .unwrap_or_else(|| panic!("{target:?} is not on screen"))
            .1;
        f.pointer = (hit.x + hit.w * 0.5, hit.y + hit.h * 0.5);
        f.click(None, 1280.0, 720.0)
    }

    #[test]
    fn the_busiest_open_matching_room_is_chosen() {
        let rooms = vec![
            room("a", "hd_dust2", "dm", 2),
            room("b", "hd_dust2", "dm", 6),
            room("c", "hd_aim", "dm", 9),
            room("full", "hd_dust2", "dm", 10),
            room("rated", "hd_dust2", "duel", 1),
            room("t", "hd_dust2", "tdm", 8),
        ];
        assert_eq!(choose_room(&rooms, None, None).unwrap().id, "c");
        assert_eq!(choose_room(&rooms, Some("hd_dust2"), None).unwrap().id, "t");
        assert_eq!(
            choose_room(&rooms, Some("hd_dust2"), Some("dm"))
                .unwrap()
                .id,
            "b"
        );
        // A full room and a rated one are never an answer.
        assert!(choose_room(&rooms[3..5], None, None).is_none());
        // Nor is one nobody is in: it is about to close.
        assert!(choose_room(&[room("empty", "hd_dust2", "dm", 0)], None, None).is_none());
        assert!(choose_room(&rooms, Some("hd_nuke"), None).is_none());
        // A tie goes to the first listed, every time.
        let tied = vec![room("first", "m", "dm", 4), room("second", "m", "dm", 4)];
        assert_eq!(choose_room(&tied, None, None).unwrap().id, "first");
    }

    #[test]
    fn a_room_listed_without_a_size_still_has_seats() {
        let mut r = room("a", "m", "dm", 3);
        r.seats = 0;
        assert!(r.open());
        r.players = DEFAULT_SEATS;
        assert!(!r.open());
    }

    #[test]
    fn find_match_carries_exactly_what_is_selected() {
        let mut f = front();
        assert_eq!(
            click(&mut f, Target::Go),
            Some(Intent::FindMatch {
                maps: vec![],
                mode: None,
                bots: 5
            })
        );
        click(&mut f, Target::Map(Some(2)));
        click(&mut f, Target::Mode(1));
        click(&mut f, Target::Bots(-1));
        assert_eq!(
            f.key(KeyCode::Enter),
            Some(Intent::FindMatch {
                maps: vec!["hd_dust2".into()],
                mode: Some("dm".into()),
                bots: 4
            })
        );
        // The mode wraps back round to "any".
        click(&mut f, Target::Mode(1));
        click(&mut f, Target::Mode(1));
        assert_eq!(f.mode, 0);
    }

    #[test]
    fn quick_play_tiles_toggle_and_a_search_takes_all_that_are_on() {
        let mut f = front();
        click(&mut f, Target::Map(Some(2)));
        click(&mut f, Target::Map(Some(0)));
        assert_eq!(
            f.picked_maps(),
            ["hd_dust2", "hd_aim"],
            "in the order picked"
        );
        assert!(f.selection_label().starts_with("2 MAPS"));
        // A second click turns one off again.
        click(&mut f, Target::Map(Some(2)));
        assert_eq!(f.picked_maps(), ["hd_aim"]);
        assert!(f.selection_label().starts_with("AIM"));
        click(&mut f, Target::Map(Some(1)));
        assert_eq!(
            click(&mut f, Target::Go),
            Some(Intent::FindMatch {
                maps: vec!["hd_aim".into(), "hd_assault".into()],
                mode: None,
                bots: 5
            })
        );
        // ANY MAP clears them, and is what is on when none are.
        click(&mut f, Target::Map(None));
        assert!(f.picked_maps().is_empty());
        assert!(f.map_active(None) && !f.map_active(Some(0)));
        // Practice is one map: its tiles select, they do not toggle.
        click(&mut f, Target::Kind(PlayKind::Practice));
        click(&mut f, Target::Map(Some(1)));
        click(&mut f, Target::Map(Some(2)));
        assert!(f.map_active(Some(2)) && !f.map_active(Some(1)));
    }

    #[test]
    fn a_lobby_is_made_or_joined_by_code_and_only_its_leader_searches() {
        let mut f = front();
        assert_eq!(
            click(&mut f, Target::LobbyCreate),
            Some(Intent::LobbyCreate)
        );
        // Joining: a field, typed in any case, cut to length, sent on Enter.
        assert_eq!(click(&mut f, Target::LobbyJoin), None);
        assert!(f.text("k7-m2"));
        assert_eq!(f.entering_code.as_deref(), Some("K7M2"));
        assert_eq!(f.key(KeyCode::Enter), None, "four characters is not a code");
        // A digit here is part of the code, never a tab shortcut.
        f.text("3xyz");
        assert_eq!(f.key(KeyCode::Digit3), None);
        assert_eq!(f.tab, Tab::Play);
        assert_eq!(f.entering_code.as_deref(), Some("K7M23"));
        assert_eq!(
            f.key(KeyCode::Enter),
            Some(Intent::LobbyJoin("K7M23".into()))
        );
        assert_eq!(f.entering_code, None);

        let member = |name: &str, leader, you| LobbyMember {
            name: name.into(),
            leader,
            you,
        };
        f.lobby = Some(LobbyView {
            code: "K7M23".into(),
            members: vec![
                member("Sable", true, false),
                member("Operative-7", false, true),
            ],
            leader: false,
            serial: 0,
            found: None,
        });
        // Not the leader: the button explains instead of searching.
        assert_eq!(click(&mut f, Target::Go), None);
        assert!(f
            .notice
            .as_ref()
            .is_some_and(|(why, _)| why.contains("SABLE")));
        assert_eq!(click(&mut f, Target::LobbyLeave), Some(Intent::LobbyLeave));
        // The leader's button searches for everyone, with what is toggled on.
        f.lobby.as_mut().unwrap().leader = true;
        click(&mut f, Target::Map(Some(2)));
        assert_eq!(
            click(&mut f, Target::Go),
            Some(Intent::LobbyPlay {
                maps: vec!["hd_dust2".into()],
                mode: None,
            })
        );
        assert!(f.selection_label().starts_with("LOBBY OF 2"));
        // And both states of the panel draw.
        let mut out = Vec::new();
        f.paint(None, 1280.0, 720.0, &mut out);
        assert!(!out.is_empty());
    }

    #[test]
    fn pictures_go_behind_the_menu_and_under_each_tile_that_has_one() {
        let mut f = front();
        assert!(
            f.images(None, 1280.0, 720.0).is_empty(),
            "nothing has loaded"
        );
        f.art = vec!["hd_dust2".into(), "hd_assault".into()];
        let quads = f.images(None, 1280.0, 720.0);
        // The backdrop first, the whole window, and dark.
        assert_eq!((quads[0].rect.w, quads[0].rect.h), (1280.0, 720.0));
        assert!(quads[0].tint[0] < 0.4);
        // Then one per map tile; a map with no picture still gets its quad,
        // which the atlas simply does not draw.
        assert_eq!(quads.len(), 1 + f.maps.len());
        // The backdrop follows the last map toggled on.
        click(&mut f, Target::Map(Some(1)));
        click(&mut f, Target::Map(Some(2)));
        assert_eq!(f.images(None, 1280.0, 720.0)[0].map, "hd_dust2");
        // A chosen tile is drawn at full brightness, the rest held back.
        let quads = f.images(None, 1280.0, 720.0);
        let lit = |name: &str| quads[1..].iter().find(|q| q.map == name).unwrap().tint[0];
        assert!(lit("hd_dust2") > lit("hd_aim"));
        // Loading shows the map being loaded, and no tiles.
        f.begin_loading("hd_assault", "DEATHMATCH", "NEW ROOM", &["FETCH"]);
        let quads = f.images(None, 1280.0, 720.0);
        assert_eq!(quads.len(), 1);
        assert_eq!(quads[0].map, "hd_assault");
        // And a map with no picture loads over the plain slab.
        f.begin_loading("hd_aim", "", "", &["FETCH"]);
        assert!(f.images(None, 1280.0, 720.0).is_empty());
    }

    #[test]
    fn practice_needs_one_map_and_picks_one_when_any_was_selected() {
        let mut f = front();
        assert_eq!(f.map, None);
        click(&mut f, Target::Kind(PlayKind::Practice));
        assert_eq!(f.map, Some(0));
        assert_eq!(
            click(&mut f, Target::Go),
            Some(Intent::Practice {
                map: "hd_aim".into()
            })
        );
        // And the ANY tile is gone, so it cannot be reselected.
        assert!(!f
            .hits(None, 1280.0, 720.0)
            .iter()
            .any(|(t, _)| *t == Target::Map(None)));
    }

    #[test]
    fn browsing_asks_for_the_list_and_joins_the_selected_room() {
        let mut f = front();
        assert_eq!(
            click(&mut f, Target::Kind(PlayKind::Browse)),
            Some(Intent::RefreshRooms)
        );
        // Nothing to join yet, so the button does nothing rather than guessing.
        assert_eq!(click(&mut f, Target::Go), None);
        f.rooms = vec![
            room("r1", "hd_aim", "dm", 2),
            room("r2", "hd_dust2", "tdm", 5),
        ];
        click(&mut f, Target::Room(1));
        assert_eq!(f.room, 1);
        // A second click on the selected row joins it.
        assert_eq!(
            click(&mut f, Target::Room(1)),
            Some(Intent::Join {
                room: "r2".into(),
                map: "hd_dust2".into(),
                mode: "tdm".into()
            })
        );
    }

    #[test]
    fn the_arrow_keys_walk_every_map_tile_and_wrap() {
        let mut f = front();
        let mut seen = vec![f.map];
        for _ in 0..3 {
            f.key(KeyCode::ArrowRight);
            seen.push(f.map);
        }
        assert_eq!(seen, vec![None, Some(0), Some(1), Some(2)]);
        f.key(KeyCode::ArrowRight);
        assert_eq!(f.map, None, "past the last tile is the first again");
        f.key(KeyCode::ArrowLeft);
        assert_eq!(f.map, Some(2));
    }

    #[test]
    fn stepping_a_loadout_slot_asks_for_it_to_be_saved() {
        let mut f = front();
        click(&mut f, Target::Tab(Tab::Loadout));
        let saved = click(&mut f, Target::Step(0, 1));
        let Some(Intent::Save(key, raw)) = saved else {
            panic!("expected a save, got {saved:?}");
        };
        assert_eq!(key, "hassault.loadout");
        assert!(raw.contains("raider"), "{raw}");
        assert_eq!(f.slot_name(0), "RAIDER");
        // The other side never had the raider on offer.
        f.key(KeyCode::Space);
        assert_eq!(f.side, 1);
        assert_eq!(f.slot_name(0), "ASSAULT");
        assert_eq!(f.slot_position(0), (1, 1));
    }

    #[test]
    fn a_callsign_is_typed_cut_to_length_and_saved_on_enter() {
        let mut f = front();
        click(&mut f, Target::Callsign);
        assert!(f.editing.is_some());
        for _ in 0..40 {
            f.key(KeyCode::Backspace);
        }
        assert!(f.text("A very long callsign indeed"));
        assert_eq!(f.editing.as_deref().unwrap().chars().count(), CALLSIGN_LEN);
        // A digit typed into the field is a character, not the tab shortcut.
        assert_eq!(f.key(KeyCode::Digit2), None);
        assert_eq!(f.tab, Tab::Play);
        let saved = f.key(KeyCode::Enter);
        assert_eq!(saved, Some(Intent::SaveCallsign("A very long call".into())));
        assert_eq!(f.callsign, "A very long call");
        // Unchanged, or emptied: nothing to save.
        click(&mut f, Target::Callsign);
        assert_eq!(f.key(KeyCode::Enter), None);
        assert!(!f.text("x"), "no field is open, so the text is not taken");
    }

    #[test]
    fn progress_counts_finished_phases_and_half_of_a_running_one() {
        let mut f = front();
        f.begin_loading("hd_dust2", "DEATHMATCH", "NEW ROOM", &["A", "B", "C", "D"]);
        assert_eq!(f.progress(), 0.0);
        f.step(0, StepState::Done, "ok");
        f.step(1, StepState::Running, "");
        assert!((f.progress() - 0.375).abs() < 1e-6);
        f.step(1, StepState::Skipped, "");
        assert!((f.progress() - 0.5).abs() < 1e-6);
        // A phase this screen does not list is ignored, not a panic.
        f.step(9, StepState::Done, "");
        assert_eq!(f.steps[0].note, "ok");
    }

    #[test]
    fn a_dev_tool_starts_on_one_map_with_what_it_needs() {
        let mut f = front();
        f.drills = vec![
            DrillEntry {
                id: "flick".into(),
                name: "Flick".into(),
                map: Some("hd_aim".into()),
            },
            DrillEntry {
                id: "track".into(),
                name: "Track".into(),
                map: None,
            },
        ];
        f.bench_maps = vec!["hd_dust2".into()];
        click(&mut f, Target::Tab(Tab::Dev));
        // A drill built on a map runs there, whatever tile is selected.
        click(&mut f, Target::Map(Some(2)));
        assert_eq!(
            click(&mut f, Target::Go),
            Some(Intent::Tool {
                tool: DevTool::Drill,
                map: "hd_aim".into(),
                drill: Some("flick".into()),
            })
        );
        // One that runs anywhere takes the selected map.
        click(&mut f, Target::Drill(1));
        assert_eq!(
            click(&mut f, Target::Go),
            Some(Intent::Tool {
                tool: DevTool::Drill,
                map: "hd_dust2".into(),
                drill: Some("track".into()),
            })
        );
        click(&mut f, Target::Tool(DevTool::Bench));
        assert_eq!(
            click(&mut f, Target::Go),
            Some(Intent::Tool {
                tool: DevTool::Bench,
                map: "hd_dust2".into(),
                drill: None,
            })
        );
        // A map with no benchmark path says so instead of starting.
        click(&mut f, Target::Map(Some(0)));
        assert_eq!(click(&mut f, Target::Go), None);
        let (why, error) = f.notice.clone().expect("a reason");
        assert!(
            error && why.contains("NO BENCHMARK PATH") && why.contains("DUST2"),
            "{why}"
        );
        click(&mut f, Target::Tool(DevTool::Editor));
        assert_eq!(
            f.key(KeyCode::Enter),
            Some(Intent::Tool {
                tool: DevTool::Editor,
                map: "hd_aim".into(),
                drill: None,
            })
        );
    }

    #[test]
    fn the_server_view_lists_rooms_looks_into_one_and_joins_it() {
        let mut f = front();
        click(&mut f, Target::Tab(Tab::Dev));
        // Opening it asks for the list; with nothing running it will not join.
        assert_eq!(
            click(&mut f, Target::Tool(DevTool::Server)),
            Some(Intent::RefreshRooms)
        );
        assert_eq!(click(&mut f, Target::Go), None);
        assert!(f
            .notice
            .as_ref()
            .is_some_and(|(why, _)| why.contains("NO ROOM")));
        f.rooms = vec![
            room("r1", "hd_aim", "dm", 2),
            room("r2", "hd_dust2", "tdm", 5),
        ];
        // Selecting a room asks who is in it; the map tiles are not here.
        // Clicking the row that is already selected looks too; it never joins.
        assert_eq!(
            click(&mut f, Target::Room(0)),
            Some(Intent::Roster("r1".into()))
        );
        assert_eq!(
            click(&mut f, Target::Room(1)),
            Some(Intent::Roster("r2".into()))
        );
        assert_eq!(
            click(&mut f, Target::Room(1)),
            Some(Intent::Roster("r2".into()))
        );
        assert!(!f
            .hits(None, 1280.0, 720.0)
            .iter()
            .any(|(t, _)| matches!(t, Target::Map(_))));
        assert_eq!(
            click(&mut f, Target::Go),
            Some(Intent::Join {
                room: "r2".into(),
                map: "hd_dust2".into(),
                mode: "tdm".into(),
            })
        );
        // A roster for another room is not shown as this one's.
        f.roster = Some(RoomDetail {
            id: "r1".into(),
            host: "p1".into(),
            roster: vec![RosterRow {
                id: "p1".into(),
                name: "Sable".into(),
                kills: 3,
                deaths: 1,
                bot: false,
            }],
        });
        assert!(f.shown_roster().is_none(), "r1's roster shown under r2");
        f.room = 0;
        assert_eq!(f.shown_roster().map(|d| d.roster.len()), Some(1));
        // And both states draw.
        for room in [0, 1] {
            f.room = room;
            let mut out = Vec::new();
            f.paint(None, 1280.0, 720.0, &mut out);
            assert!(!out.is_empty());
        }
    }

    #[test]
    fn the_model_viewer_opens_on_the_selected_map() {
        let mut f = front();
        click(&mut f, Target::Tab(Tab::Dev));
        click(&mut f, Target::Tool(DevTool::Viewer));
        click(&mut f, Target::Map(Some(1)));
        assert_eq!(
            click(&mut f, Target::Go),
            Some(Intent::Tool {
                tool: DevTool::Viewer,
                map: "hd_assault".into(),
                drill: None,
            })
        );
    }

    #[test]
    fn dev_overlays_toggle_and_the_keys_reach_every_tool() {
        let mut f = front();
        click(&mut f, Target::Tab(Tab::Dev));
        assert_eq!(f.overlays, [false; DEV_OVERLAYS.len()]);
        click(&mut f, Target::Overlay(1));
        assert_eq!(f.overlays, [false, true, false]);
        click(&mut f, Target::Overlay(1));
        assert_eq!(f.overlays, [false; DEV_OVERLAYS.len()]);
        let mut seen = vec![f.tool];
        for _ in 1..DevTool::ALL.len() {
            f.key(KeyCode::ArrowDown);
            seen.push(f.tool);
        }
        // And round again, both ways.
        f.key(KeyCode::ArrowDown);
        assert_eq!(f.tool, DevTool::Drill);
        f.key(KeyCode::ArrowUp);
        assert_eq!(f.tool, DevTool::Server);
        assert_eq!(seen, DevTool::ALL);
        // No ANY MAP here: the arrows walk real maps only.
        f.map = None;
        f.key(KeyCode::ArrowRight);
        assert_eq!(f.map, Some(1));
        // With no drills listed, the drill does not start and says why.
        f.tool = DevTool::Drill;
        assert_eq!(f.key(KeyCode::Enter), None);
        assert!(f.notice.is_some());
    }

    #[test]
    fn nothing_clickable_overlaps_or_leaves_the_window() {
        let mut f = front();
        f.rooms = vec![
            room("r1", "hd_aim", "dm", 2),
            room("r2", "hd_dust2", "tdm", 5),
        ];
        f.notice = Some(("the room closed".into(), true));
        for (w, h) in [
            (1280.0, 720.0),
            (1600.0, 900.0),
            (1920.0, 1080.0),
            (3840.0, 2160.0),
        ] {
            for tab in [Tab::Play, Tab::Loadout, Tab::Dev] {
                for (n, tool) in DevTool::ALL.into_iter().enumerate() {
                    let kind = PlayKind::ALL[n % PlayKind::ALL.len()];
                    f.tab = tab;
                    f.kind = kind;
                    f.tool = tool;
                    let hits = f.hits(None, w, h);
                    for (target, r) in &hits {
                        assert!(
                            r.x >= 0.0
                                && r.y >= 0.0
                                && r.x + r.w <= w + 0.5
                                && r.y + r.h <= h + 0.5,
                            "{target:?} leaves a {w}x{h} window: {r:?}"
                        );
                    }
                    for (i, (a, ra)) in hits.iter().enumerate() {
                        for (b, rb) in hits.iter().skip(i + 1) {
                            // A slot card deliberately contains its two arrows:
                            // they are listed first, so they win the click.
                            if matches!((a, b), (Target::Step(..), Target::Slot(_))) {
                                continue;
                            }
                            let apart = ra.x + ra.w <= rb.x + 0.5
                                || rb.x + rb.w <= ra.x + 0.5
                                || ra.y + ra.h <= rb.y + 0.5
                                || rb.y + rb.h <= ra.y + 0.5;
                            assert!(apart, "{a:?} and {b:?} overlap at {w}x{h}");
                        }
                    }
                }
            }
        }
    }

    #[test]
    fn every_screen_draws_and_nothing_lands_off_the_window() {
        let mut f = front();
        f.rooms = vec![room("r1", "hd_aim", "dm", 2)];
        let screens: Vec<Box<dyn Fn(&mut Front)>> = vec![
            Box::new(|f| {
                f.show(Screen::Boot);
                f.steps = vec![Step::waiting("WEAPONS")];
                f.boot_failed = Some("timed out".into());
            }),
            Box::new(|f| f.show(Screen::Home)),
            Box::new(|f| {
                f.show(Screen::Home);
                f.tab = Tab::Loadout;
            }),
            Box::new(|f| {
                f.show(Screen::Home);
                f.tab = Tab::Loadout;
                f.slot = 2;
            }),
            Box::new(|f| {
                f.show(Screen::Home);
                f.tab = Tab::Dev;
                f.overlays[1] = true;
            }),
            Box::new(|f| {
                f.tab = Tab::Play;
                f.begin_search();
            }),
            Box::new(|f| {
                f.begin_loading("hd_dust2", "DEATHMATCH", "NEW ROOM", &["FETCH", "BUILD"]);
                f.loading.plan = vec![Run {
                    y: 1.0,
                    x0: 0.0,
                    x1: 4.0,
                    band: 1,
                }];
                f.loading.plan_size = 8.0;
            }),
        ];
        for set in screens {
            set(&mut f);
            f.clock += 5.0;
            let mut out = Vec::new();
            f.paint(None, 1280.0, 720.0, &mut out);
            assert!(!out.is_empty(), "{:?} drew nothing", f.screen);
            for v in &out {
                assert!(
                    v.position[0].abs() <= 1.02 && v.position[1].abs() <= 1.02,
                    "{:?} drew outside the window: {:?}",
                    f.screen,
                    v.position
                );
            }
        }
    }

    #[test]
    fn every_fixed_label_is_in_the_fallback_font() {
        // Until the system fonts have loaded — and for good on a machine that
        // has none of them — text is drawn in the 5x7 bitmap font, where a
        // character with no glyph draws nothing at all. A tip with a hole in
        // it reads as a typo, not as a missing font.
        let mut labels: Vec<String> = Vec::new();
        labels.extend(TIPS.iter().map(|s| s.to_string()));
        labels.extend([WORDMARK, HINT_PLAY, HINT_LOADOUT, HINT_DEV].map(String::from));
        labels.extend(DEV_OVERLAYS.map(String::from));
        for tool in DevTool::ALL {
            labels.push(tool.title().to_string());
            labels.push(tool.about().to_uppercase());
            labels.push(tool.go().to_string());
        }
        labels.extend(
            SIDE_LABELS
                .iter()
                .chain(SLOT_LABELS.iter())
                .map(|s| s.to_string()),
        );
        labels.extend(Tab::ALL.iter().map(|t| t.label().to_string()));
        for kind in PlayKind::ALL {
            labels.push(kind.title().to_string());
            labels.push(kind.about().to_uppercase());
        }
        for label in labels {
            for ch in label.chars() {
                assert!(
                    crate::hud::has_glyph(ch),
                    "no glyph for {ch:?} in {label:?}"
                );
            }
        }
    }

    #[test]
    fn a_map_name_reads_as_a_title() {
        assert_eq!(map_title("hd_dust2"), "DUST2");
        assert_eq!(map_title("ac_desert_two"), "AC DESERT TWO");
    }

    #[test]
    fn every_tip_fits_on_one_line_of_a_720p_window() {
        // 1.3k glyphs are about 7.8px wide in the bitmap metrics; the tip has
        // the window's width less two margins.
        for tip in TIPS {
            assert!(tip.len() * 8 <= 1280 - 80, "too long: {tip}");
        }
    }
}

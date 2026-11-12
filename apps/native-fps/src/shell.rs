//! The shell: the window, and whatever is in it.
//!
//! A client launched into a match builds one `App` and runs it until the
//! process ends. A client started with nowhere to go — the downloaded
//! executable, double-clicked — runs this instead: a window that opens at
//! once, holds the front end (`front.rs`), and hosts a match inside itself
//! when the player starts one.
//!
//! ```text
//!   Shell ── Front  (FrontGpu: the overlay pipeline, straight to the window)
//!        └─ Match  (App: the whole renderer, on the same window)
//! ```
//!
//! ## A match is a fresh `App`, every time
//!
//! `App` holds a few hundred fields of match state — prediction, snapshots,
//! the roster, effects, the tally. Returning to the menu by resetting them one
//! by one would be a list that is wrong the day a field is added, and the
//! symptom would be a second match that starts with the first one's score. So
//! a match is constructed, handed the window, and dropped; the next one starts
//! from nothing. The cost is a renderer start per match, which is what the
//! loading screen's last line is.
//!
//! ## Nothing that waits runs on this thread
//!
//! Every fetch, the map build, the connect and the join are on worker threads
//! that report over one channel. The window stays live through all of it —
//! that is the difference between a loading screen and a frozen one.
//!
//! Each job carries a **ticket**. A message whose ticket is not the current
//! one is from a job the player has since cancelled or superseded, and is
//! dropped; a socket that arrives that way closes on drop, which is the leave.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant};

use winit::application::ApplicationHandler;
use winit::event::{DeviceEvent, DeviceId, ElementState, MouseButton, WindowEvent};
use winit::event_loop::{ActiveEventLoop, ControlFlow, EventLoop};
use winit::keyboard::{KeyCode, PhysicalKey};
use winit::window::{CursorGrabMode, Window, WindowId};

use hassault_native::api::{
    Account, AuthPoll, DraftInfo, HitboxSpec, ItemsResponse, Matched, NodeApi, SkinDefinition,
    TacticalSpec, ThrowPhysics, WeaponSpec,
};
use hassault_native::bench::{self, BenchRun};
use hassault_native::console::Definitions;
use hassault_native::controls::Controls;
use hassault_native::front::{
    self, AccountView, CaseView, DevTool, DrillEntry, Found, Front, Intent, ItemView, ListingView,
    LobbyView, MapEntry, ModeEntry, RoomDetail, RoomEntry, Screen, SignIn, Step, StepState,
    WalletView, PROVIDERS,
};
use hassault_native::front_gpu::FrontGpu;
use hassault_native::geometry::{self, MeshData};
use hassault_native::hud::OverlayVertex;
use hassault_native::map_art::{self, ArtVertex, Atlas};
use hassault_native::menu::{self, Action, LoadoutAgent, LoadoutWeapon, Menu};
use hassault_native::net::MatchSocket;
use hassault_native::radar::{self, Run};
use hassault_native::settings::{self, Settings, SettingsWriter};
use hassault_native::text_kit::TextKit;
use hassault_native::viewmodel::{self, Skin};
use hassault_native::world::World;

use crate::app::{App, Ended};
use crate::Args;

/// How long a found match stays on screen before the load begins: long enough
/// to read which map it is, and not a wait anyone would call one.
const FOUND_BEAT: Duration = Duration::from_millis(1100);
/// How often the room list is refreshed while the menu is showing.
const ROOMS_EVERY: Duration = Duration::from_secs(10);
/// How often the lobby is asked for. Well inside the silence after which
/// the server drops a member (`hassault_lobby.MEMBER_SILENCE_S`, 12 s), in a
/// match as well as out of one.
const LOBBY_EVERY: Duration = Duration::from_millis(1500);
const LOBBY_EVERY_IN_MATCH: Duration = Duration::from_secs(4);
/// How long a sign-in may sit unfinished in the browser before it is
/// given up on. The server's own pending login lasts no longer.
const SIGNIN_PATIENCE: Duration = Duration::from_secs(600);
/// How long the server has to answer a join.
const JOIN_PATIENCE: Duration = Duration::from_secs(12);

const BOOT_STEPS: [&str; 6] = [
    "WEAPONS",
    "MAPS AND GAMES",
    "GRENADES AND ITEMS",
    "OPERATORS AND SKINS",
    "CONSOLE COMMANDS",
    "OPEN ROOMS",
];
const ONLINE_STEPS: [&str; 5] = [
    "FETCH THE MAP",
    "BUILD THE WORLD",
    "CONNECT",
    "JOIN THE ROOM",
    "START THE RENDERER",
];
const OFFLINE_STEPS: [&str; 3] = ["FETCH THE MAP", "BUILD THE WORLD", "START THE RENDERER"];

/// What every match is built from, fetched once.
///
/// The same list `main.rs` fetches before a direct launch, for the same
/// reason: each is a number the server simulates with, and a local copy is a
/// client that disagrees with it.
#[derive(Clone)]
struct Tables {
    weapons: Vec<WeaponSpec>,
    tacticals: Vec<TacticalSpec>,
    throw_physics: Option<ThrowPhysics>,
    items: ItemsResponse,
    hitbox: HitboxSpec,
    skins: HashMap<String, Skin>,
    definitions: Definitions,
    agents: Vec<LoadoutAgent>,
}

struct Booted {
    tables: Tables,
    maps: Vec<MapEntry>,
    modes: Vec<ModeEntry>,
    rooms: Vec<RoomEntry>,
    drills: Vec<DrillEntry>,
}

/// What a developer tool adds to a practice load.
#[derive(Debug, Clone)]
enum ToolPlan {
    /// Run this aim drill once the range is up.
    Drill(String),
    /// Fly the map's benchmark path and come back with the numbers.
    Bench,
    /// Open a draft of the map and edit that.
    Edit,
    /// The model viewer: the range, with every weapon and finish in reach.
    Viewer,
}

/// One match to load: where, and as what.
#[derive(Debug, Clone)]
struct LoadPlan {
    map: String,
    /// A room to join by id; empty joins or opens one on the map.
    room: String,
    /// The game, by id; empty is the server's default.
    mode: String,
    /// Bots to field once seated. Only asked for in a room this opened.
    bots: u32,
    /// `false` is practice: no socket at all.
    online: bool,
    /// The room is this search's own: opened for it, or about to be by the
    /// join. The first seat in it may tune it and field bots.
    own: bool,
    /// A developer tool to start instead of free practice.
    tool: Option<ToolPlan>,
}

/// The account's things, as one fetch.
struct AccountData {
    items: Vec<ItemView>,
    wallet: Option<WalletView>,
    listings: Vec<ListingView>,
    /// What the action that prompted this did: said, or why it was refused.
    note: Result<Option<String>, String>,
}

/// Something to do as the signed-in account, before fetching it again.
enum AccountJob {
    Refresh,
    Equip(String),
    Unequip(String),
    BuyKey,
    OpenCase(String),
    BuyListing(String),
}

/// What a search came back with.
enum Search {
    /// The server chose (`NodeApi::matchmake`).
    Matched(Matched),
    /// A server with no matchmaking route: the room list, to choose from here
    /// as this client did before the server could.
    Listed(Vec<RoomEntry>),
}

struct Loaded {
    world: World,
    mesh: MeshData,
    plan: Vec<Run>,
    socket: Option<MatchSocket>,
    load: LoadPlan,
    /// The draft being edited and who painted each cell, for the map editor.
    draft: Option<(DraftInfo, Vec<u16>)>,
    /// Every skin design, for the model viewer; empty otherwise.
    catalog: Vec<SkinDefinition>,
}

enum Msg {
    /// A boot or load phase moved on.
    Step(usize, StepState, String),
    Booted(Box<Booted>),
    BootFailed(String),
    /// The room list, for the menu.
    Rooms(Result<Vec<RoomEntry>, String>),
    /// The room list, as the answer to a search.
    Searched(Result<Search, String>),
    /// Who is in a room, for the server view; `None` when it has closed.
    Roster(Option<RoomDetail>),
    /// The map art, decoded.
    Art(Arc<Atlas>),
    /// The lobby, as the server describes it. The token rides along when this
    /// is the answer to entering one.
    Lobby(Result<(Option<String>, LobbyView), String>),
    /// The server no longer has this player in a lobby.
    LobbyGone,
    /// Which providers the server can sign in with.
    Providers([bool; 2]),
    /// The stored token's account, at boot; `None` when it is no longer good.
    Account(Option<Account>),
    /// A sign-in is under way: finish it at `url`, with `code` if there is one.
    SignInStep {
        provider: usize,
        url: String,
        code: Option<String>,
    },
    SignedIn(String, Account),
    SignInFailed(String),
    /// The account's things, fetched, and what the last action did.
    AccountData(Box<AccountData>),
    /// The server stopped accepting the token.
    SignedOut(String),
    /// The map's floor plan, as soon as the world is built.
    Plan(Vec<Run>, f32),
    Loaded(Box<Loaded>),
    LoadFailed(String),
}

enum Stage {
    Front,
    Match(Box<App>),
}

pub struct Shell {
    server: String,
    callsign: String,
    /// The game server's sign-in token; `None` is a guest.
    token: Option<String>,
    /// Counts sign-ins started and cancelled, so a worker for one that was
    /// cancelled or replaced stops asking.
    signin_gen: Arc<AtomicU64>,
    window: Option<Arc<Window>>,
    gpu: Option<FrontGpu>,
    kit: TextKit,
    front: Front,
    /// The settings pages, over the front end.
    menu: Menu,
    settings: Settings,
    controls: Controls,
    writer: SettingsWriter,
    tables: Option<Tables>,
    stage: Stage,
    tx: Sender<(u64, Msg)>,
    rx: Receiver<(u64, Msg)>,
    /// The current job. See the module docs.
    ticket: u64,
    /// What the running search was asked for: maps, game, bots.
    asked: Option<(Vec<String>, Option<String>, u32)>,
    /// The map art, once decoded; kept to hand to each new `FrontGpu`.
    atlas: Option<Arc<Atlas>>,
    art_verts: Vec<ArtVertex>,
    /// The lobby this player is in: its code, and their token.
    lobby: Option<(String, String)>,
    /// The last lobby search acted on, so each is joined once.
    lobby_serial: u64,
    last_lobby: Instant,
    /// A search has an answer; the load starts when the beat is over.
    found: Option<(Instant, LoadPlan)>,
    /// A match is loaded; frames still to draw before the renderer starts.
    ready: Option<(Box<Loaded>, u8)>,
    last_frame: Instant,
    last_rooms: Instant,
    overlay: Vec<OverlayVertex>,
}

/// Run the front end until the window closes.
pub fn run(args: &Args) -> Result<(), Box<dyn std::error::Error>> {
    let event_loop = EventLoop::new()?;
    let mut shell = Shell::new(args);
    event_loop.run_app(&mut shell)?;
    shell.shutdown();
    Ok(())
}

/// `https://horrible-games.fly.dev/` as the name a player would say.
fn host_label(server: &str) -> String {
    server
        .trim_start_matches("https://")
        .trim_start_matches("http://")
        .trim_end_matches('/')
        .to_string()
}

fn account_view(account: &Account) -> AccountView {
    AccountView {
        name: if account.display_name.is_empty() {
            account.id.clone()
        } else {
            account.display_name.clone()
        },
        handle: account.handle.clone().filter(|h| !h.is_empty()),
    }
}

/// Whether `url` is something to hand the system's browser: a web address,
/// with nothing in it a shell or a launcher would read as more than one.
fn is_web_url(url: &str) -> bool {
    (url.starts_with("https://") || url.starts_with("http://"))
        && url.len() < 2048
        && !url
            .chars()
            .any(|c| c.is_whitespace() || c.is_control() || c == '"')
}

/// Open `url` in the player's browser. Best effort, and silent when it
/// fails: the caller has put the address on screen as well.
fn open_url(url: &str) -> bool {
    if !is_web_url(url) {
        return false;
    }
    use std::process::{Command, Stdio};
    // No shell in between on any platform: the address is an argument, never
    // a command line, so nothing in it is parsed.
    #[cfg(windows)]
    let mut command = {
        let mut c = Command::new("rundll32");
        c.args(["url.dll,FileProtocolHandler", url]);
        c
    };
    #[cfg(target_os = "macos")]
    let mut command = {
        let mut c = Command::new("open");
        c.arg(url);
        c
    };
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut command = {
        let mut c = Command::new("xdg-open");
        c.arg(url);
        c
    };
    command
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .is_ok()
}

/// A benchmark's result line (`bench::BenchResult`, as JSON) as one line of
/// the menu: what was measured, and the two numbers people ask for.
fn bench_summary(line: &str) -> String {
    let v: serde_json::Value = serde_json::from_str(line).unwrap_or(serde_json::Value::Null);
    let text = |key: &str| {
        v.get(key)
            .and_then(|x| x.as_str())
            .unwrap_or("?")
            .to_string()
    };
    let num = |key: &str| v.get(key).and_then(|x| x.as_f64()).unwrap_or(0.0);
    format!(
        "BENCHMARK  {}  /  {}  /  {}X{}  -  {:.0} FPS, P99 {:.1} MS, {} FRAMES",
        front::map_title(&text("map")),
        text("tier").to_uppercase(),
        num("width") as u32,
        num("height") as u32,
        num("fps"),
        num("p99"),
        num("frames") as u64,
    )
}

/// ` WITH 3 BOTS`, or nothing.
fn bots_label(bots: u32) -> String {
    match bots {
        0 => String::new(),
        1 => " WITH 1 BOT".into(),
        n => format!(" WITH {n} BOTS"),
    }
}

fn short_room(room: &str) -> String {
    room.chars().take(8).collect::<String>().to_uppercase()
}

impl Shell {
    fn new(args: &Args) -> Shell {
        // The same document a direct standalone launch reads (`main.rs`): the
        // local settings file, with this run's `--set` rows laid over it.
        let path = settings::local_settings_path();
        if path.is_none() {
            eprintln!("hassault: warning — no config directory; settings will not be saved");
        }
        let mut values = path
            .as_deref()
            .map(settings::read_local)
            .unwrap_or_else(|| serde_json::json!({}));
        let (callsign, fresh) =
            crate::guest_callsign(args.name_given.then_some(args.name.as_str()), &values);
        if let Some(bag) = values.as_object_mut() {
            for (key, value) in &args.overrides {
                bag.insert(key.clone(), value.clone());
            }
        }
        let mut settings = Settings::from_values(&values);
        crate::apply_run_flags(&mut settings, args);
        let controls = Controls::from_values(&values);
        let writer = match path {
            Some(path) => SettingsWriter::to_file(path),
            None => SettingsWriter::disabled(),
        };
        if fresh {
            writer.save(
                settings::KEY_CALLSIGN,
                serde_json::Value::String(callsign.clone()),
            );
        }
        let document = |key: &str| {
            values
                .get(key)
                .and_then(|v| v.as_str())
                .and_then(|raw| serde_json::from_str(raw).ok())
                .unwrap_or(serde_json::Value::Null)
        };
        let mut front = Front::new(&host_label(&args.server), &callsign);
        let token = values
            .get(settings::KEY_TOKEN)
            .and_then(|v| v.as_str())
            .filter(|t| !t.is_empty())
            .map(str::to_string);
        front.loadout.choice = document("hassault.loadout");
        front.loadout.agent_choice = document("hassault.agents");
        let mut menu = Menu::default();
        menu.front = true;
        menu.local = true;
        let (tx, rx) = mpsc::channel();
        Shell {
            server: args.server.clone(),
            callsign,
            token,
            signin_gen: Arc::new(AtomicU64::new(0)),
            window: None,
            gpu: None,
            kit: TextKit::from_env(),
            front,
            menu,
            settings,
            controls,
            writer,
            tables: None,
            stage: Stage::Front,
            tx,
            rx,
            ticket: 0,
            asked: None,
            atlas: None,
            art_verts: Vec::new(),
            lobby: None,
            lobby_serial: 0,
            last_lobby: Instant::now(),
            found: None,
            ready: None,
            last_frame: Instant::now(),
            last_rooms: Instant::now(),
            overlay: Vec::new(),
        }
    }

    /// Every queued setting reaches the file before the process ends. The
    /// match's handle on the writer is dropped first: the thread only stops
    /// once every handle has let go.
    fn shutdown(&mut self) {
        self.stage = Stage::Front;
        self.writer.finish();
    }

    fn size(&self) -> (f32, f32) {
        self.gpu
            .as_ref()
            .map(|gpu| gpu.size())
            .map(|(w, h)| (w as f32, h as f32))
            .unwrap_or((1280.0, 720.0))
    }

    /// A new job: everything still in flight for the old one is now stale.
    fn next_ticket(&mut self) -> u64 {
        self.ticket += 1;
        self.found = None;
        self.ready = None;
        self.ticket
    }

    // -- boot -------------------------------------------------------------------

    fn start_boot(&mut self) {
        let ticket = self.next_ticket();
        self.front.boot_failed = None;
        self.front.steps = BOOT_STEPS.iter().map(|s| Step::waiting(s)).collect();
        self.front.show(Screen::Boot);
        let (server, tx) = (self.server.clone(), self.tx.clone());
        spawn("hassault-boot", move || boot(&server, ticket, &tx));
    }

    fn booted(&mut self, booted: Booted) {
        let Booted {
            tables,
            maps,
            modes,
            rooms,
            drills,
        } = booted;
        self.front.drills = drills;
        self.front.bench_maps = bench::maps();
        self.account_boot();
        self.front.weapons = tables.weapons.clone();
        self.front.loadout.weapons = tables
            .weapons
            .iter()
            .filter(|w| w.slot == "primary" || w.slot == "secondary")
            .map(|w| LoadoutWeapon {
                id: w.id.clone(),
                name: w.name.clone(),
                slot: w.slot.clone(),
                side: w.side.clone(),
            })
            .collect();
        self.front.loadout.agents = tables.agents.clone();
        self.front.maps = maps;
        self.front.modes = modes;
        self.front.rooms = rooms;
        self.tables = Some(tables);
        self.last_rooms = Instant::now();
        self.front.show(Screen::Home);
    }

    // -- rooms ------------------------------------------------------------------

    fn refresh_rooms(&mut self) {
        self.last_rooms = Instant::now();
        // Ticket 0: the list is welcome whatever else is going on.
        let (server, tx) = (self.server.clone(), self.tx.clone());
        spawn("hassault-rooms", move || {
            let rooms = NodeApi::new(&server).rooms().map_err(|e| e.to_string());
            let _ = tx.send((0, Msg::Rooms(rooms)));
        });
    }

    /// Ask who is in `room`, for the server view. Ticket 0, like the list.
    fn fetch_roster(&mut self, room: String) {
        let (server, tx) = (self.server.clone(), self.tx.clone());
        spawn("hassault-roster", move || {
            let detail = NodeApi::new(&server).room_detail(&room).ok();
            let _ = tx.send((0, Msg::Roster(detail)));
        });
    }

    /// Whether the DEV TOOLS server view is what is on screen.
    fn watching_server(&self) -> bool {
        self.front.screen == Screen::Home
            && self.front.tab == front::Tab::Dev
            && self.front.tool == DevTool::Server
    }

    // -- the account ------------------------------------------------------------

    fn api(&self) -> NodeApi {
        NodeApi::new(&self.server).with_token(self.token.as_deref())
    }

    /// Once the menu is up: which sign-ins this server offers, and whether
    /// the token kept from last time is still an account.
    fn account_boot(&mut self) {
        let (node, tx, had_token) = (self.api(), self.tx.clone(), self.token.is_some());
        spawn("hassault-account", move || {
            if let Ok(providers) = node.auth_providers() {
                let _ = tx.send((0, Msg::Providers(providers)));
            }
            if !had_token {
                return;
            }
            match node.me() {
                Ok(account) => {
                    let _ = tx.send((0, Msg::Account(Some(account))));
                }
                // The server said no: the token is spent. Anything else is
                // the network, and the token is kept for next time.
                Err(hassault_native::api::ApiError::Refused(_)) => {
                    let _ = tx.send((0, Msg::Account(None)));
                }
                Err(_) => {}
            }
        });
    }

    fn sign_in(&mut self, provider: usize) {
        let Some((id, _)) = PROVIDERS.get(provider).copied() else {
            return;
        };
        let mine = self.signin_gen.fetch_add(1, Ordering::SeqCst) + 1;
        let (current, server, tx) = (
            self.signin_gen.clone(),
            self.server.clone(),
            self.tx.clone(),
        );
        spawn("hassault-signin", move || {
            let node = NodeApi::new(&server);
            let start = match node.auth_start(id) {
                Ok(start) => start,
                Err(why) => {
                    let _ = tx.send((0, Msg::SignInFailed(why)));
                    return;
                }
            };
            let _ = tx.send((
                0,
                Msg::SignInStep {
                    provider,
                    url: start.url().to_string(),
                    code: start.user_code().map(str::to_string),
                },
            ));
            let deadline = Instant::now() + SIGNIN_PATIENCE;
            loop {
                thread::sleep(Duration::from_secs(start.interval()));
                // Cancelled, or another was started: stop asking.
                if current.load(Ordering::SeqCst) != mine {
                    return;
                }
                if Instant::now() > deadline {
                    let _ = tx.send((0, Msg::SignInFailed("that sign-in ran out of time".into())));
                    return;
                }
                match node.auth_poll(id, &start) {
                    AuthPoll::Pending => {}
                    AuthPoll::Done { token, account } => {
                        if current.load(Ordering::SeqCst) == mine {
                            let _ = tx.send((0, Msg::SignedIn(token, account)));
                        }
                        return;
                    }
                    AuthPoll::Failed(why) => {
                        let _ = tx.send((0, Msg::SignInFailed(why)));
                        return;
                    }
                }
            }
        });
    }

    fn signed_in(&mut self, token: String, account: Account) {
        self.writer.save(
            settings::KEY_TOKEN,
            serde_json::Value::String(token.clone()),
        );
        self.token = Some(token);
        let view = account_view(&account);
        self.front
            .home_with(format!("SIGNED IN AS {}", view.name.to_uppercase()), false);
        self.front.account = Some(view);
        self.front.signin = SignIn::Idle;
        self.account_job(AccountJob::Refresh);
    }

    fn sign_out(&mut self, why: Option<String>) {
        self.signin_gen.fetch_add(1, Ordering::SeqCst);
        if self.token.take().is_some() {
            self.writer.save(
                settings::KEY_TOKEN,
                serde_json::Value::String(String::new()),
            );
        }
        self.front.signed_out();
        if let Some(why) = why {
            self.front.home_with(why, true);
        }
    }

    /// Do one thing as the account, then fetch what it owns again.
    fn account_job(&mut self, job: AccountJob) {
        if self.token.is_none() {
            return;
        }
        let (node, tx) = (self.api(), self.tx.clone());
        spawn("hassault-account", move || {
            use hassault_native::api::ApiError;
            let done: Result<Option<String>, ApiError> = match job {
                AccountJob::Refresh => Ok(None),
                AccountJob::Equip(id) => node.account_equip(&id).map(|_| Some("EQUIPPED".into())),
                AccountJob::Unequip(weapon) => node
                    .account_unequip(&weapon)
                    .map(|_| Some("TAKEN OFF".into())),
                AccountJob::BuyKey => node.account_buy_key().map(|_| Some("BOUGHT A KEY".into())),
                AccountJob::OpenCase(id) => node.account_open_case(&id).map(|made| {
                    let name = made
                        .definition
                        .map(|d| d.name)
                        .filter(|n| !n.is_empty())
                        .unwrap_or(made.skin_id);
                    Some(format!("THE CASE HELD {}", name.to_uppercase()))
                }),
                AccountJob::BuyListing(id) => {
                    node.account_buy_listing(&id).map(|_| Some("BOUGHT".into()))
                }
            };
            let inventory = node.account_inventory();
            // The token is the one thing every call here shares: if the
            // inventory is refused, it is the token that was.
            if let Err(ApiError::Refused(_)) = &inventory {
                let _ = tx.send((
                    0,
                    Msg::SignedOut("YOUR SIGN-IN HAS EXPIRED - SIGN IN AGAIN".into()),
                ));
                return;
            }
            let items = inventory
                .unwrap_or_default()
                .into_iter()
                .filter_map(|item| {
                    let def = item.definition?;
                    Some(ItemView {
                        instance_id: item.instance_id,
                        name: def.name,
                        weapon_id: def.weapon_id,
                        wear: item.float_value,
                        equipped: item.is_equipped,
                    })
                })
                .collect();
            let wallet = node.account_wallet().ok().map(|w| WalletView {
                balance: w.balance,
                keys: w.keys,
                key_price: w.key_price,
                cases: w
                    .cases
                    .into_iter()
                    .map(|c| CaseView {
                        instance_id: c.instance_id,
                        name: if c.name.is_empty() { c.case_id } else { c.name },
                    })
                    .collect(),
            });
            let listings = node
                .market()
                .unwrap_or_default()
                .into_iter()
                .map(|l| ListingView {
                    name: l.name(),
                    id: l.listing_id,
                    kind: l.kind,
                    price: l.price,
                })
                .collect();
            let _ = tx.send((
                0,
                Msg::AccountData(Box::new(AccountData {
                    items,
                    wallet,
                    listings,
                    note: done.map_err(|e| e.to_string()),
                })),
            ));
        });
    }

    // -- lobbies ----------------------------------------------------------------

    /// Run one lobby call off this thread and post what it answers.
    fn lobby_job(
        &self,
        name: &str,
        job: impl FnOnce(&NodeApi) -> Result<(Option<String>, LobbyView), String> + Send + 'static,
    ) {
        let (server, tx) = (self.server.clone(), self.tx.clone());
        spawn(name, move || {
            let _ = tx.send((0, Msg::Lobby(job(&NodeApi::new(&server)))));
        });
    }

    fn lobby_create(&mut self) {
        let name = self.callsign.clone();
        self.lobby_job("hassault-lobby", move |node| {
            node.lobby_create(&name)
                .map(|e| (Some(e.token), e.lobby))
                .map_err(|e| format!("could not open a lobby - {e}"))
        });
    }

    fn lobby_join(&mut self, code: String) {
        let name = self.callsign.clone();
        self.lobby_job("hassault-lobby", move |node| {
            node.lobby_join(&code, &name)
                .map(|e| (Some(e.token), e.lobby))
                .map_err(|e| format!("could not join {code} - {e}"))
        });
    }

    fn lobby_play(&mut self, maps: Vec<String>, mode: Option<String>) {
        let Some((code, token)) = self.lobby.clone() else {
            return;
        };
        self.lobby_job("hassault-lobby", move |node| {
            node.lobby_play(&code, &token, &maps, mode.as_deref())
                .map(|view| (None, view))
                .map_err(|e| format!("could not find a match - {e}"))
        });
    }

    fn lobby_leave(&mut self) {
        self.front.lobby = None;
        let Some((code, token)) = self.lobby.take() else {
            return;
        };
        let server = self.server.clone();
        spawn("hassault-lobby", move || {
            // Best effort: a member who stops asking is dropped anyway.
            let _ = NodeApi::new(&server).lobby_leave(&code, &token);
        });
    }

    /// Ask for the lobby, which is also how the server knows this player is
    /// still in it — so it is asked during a match too, only less often.
    fn poll_lobby(&mut self) {
        let every = match self.stage {
            Stage::Front => LOBBY_EVERY,
            Stage::Match(_) => LOBBY_EVERY_IN_MATCH,
        };
        let Some((code, token)) = self.lobby.clone() else {
            return;
        };
        if self.last_lobby.elapsed() < every {
            return;
        }
        self.last_lobby = Instant::now();
        let (server, tx) = (self.server.clone(), self.tx.clone());
        spawn("hassault-lobby", move || {
            match NodeApi::new(&server).lobby_state(&code, &token) {
                Ok(view) => {
                    let _ = tx.send((0, Msg::Lobby(Ok((None, view)))));
                }
                // The server said no: not a member any more, or it closed.
                Err(hassault_native::api::ApiError::Refused(_)) => {
                    let _ = tx.send((0, Msg::LobbyGone));
                }
                // Anything else is the network; the next ask may get through.
                Err(_) => {}
            }
        });
    }

    /// The lobby changed, or was just entered.
    fn lobby_seen(&mut self, token: Option<String>, view: LobbyView) {
        match token {
            Some(token) => {
                // Entering: whatever it last found was found without us.
                self.lobby = Some((view.code.clone(), token));
                self.lobby_serial = view.serial;
                self.last_lobby = Instant::now();
            }
            // An answer for a lobby since left.
            None if self.lobby.as_ref().map(|l| &l.0) != Some(&view.code) => return,
            None => {}
        }
        let fresh = view.serial > self.lobby_serial;
        self.front.lobby = Some(view.clone());
        let (Some(found), true, Screen::Home) = (view.found, fresh, self.front.screen) else {
            return;
        };
        self.lobby_serial = view.serial;
        // The room was opened for the lobby if it is new: the leader's to
        // tune and to fill, as a room their own search opened would be.
        let own = view.leader && found.opened;
        self.start_load(LoadPlan {
            map: found.map,
            room: found.id,
            mode: found.mode,
            bots: if own { self.front.bots } else { 0 },
            online: true,
            own,
            tool: None,
        });
    }

    fn start_search(&mut self, maps: Vec<String>, mode: Option<String>, bots: u32) {
        let ticket = self.next_ticket();
        self.asked = Some((maps.clone(), mode.clone(), bots));
        self.front.begin_search();
        let (server, tx) = (self.server.clone(), self.tx.clone());
        spawn("hassault-search", move || {
            let node = NodeApi::new(&server);
            let answer = match node.matchmake(&maps, mode.as_deref()) {
                Ok(matched) => Ok(Search::Matched(matched)),
                Err(e) if e.is_missing() => {
                    node.rooms().map(Search::Listed).map_err(|e| e.to_string())
                }
                Err(e) => Err(e.to_string()),
            };
            let _ = tx.send((ticket, Msg::Searched(answer)));
        });
    }

    /// The search's answer: the room the server chose.
    fn searched(&mut self, answer: Result<Search, String>) {
        let rooms = match answer {
            Ok(Search::Matched(matched)) => return self.matched(matched),
            Ok(Search::Listed(rooms)) => rooms,
            Err(why) => {
                self.front.home_with(
                    format!("COULD NOT FIND A MATCH - {why}").to_uppercase(),
                    true,
                );
                return;
            }
        };
        self.listed(rooms);
    }

    /// The server chose: a room people are in, or one it opened for this.
    fn matched(&mut self, matched: Matched) {
        let Some((_, _, bots)) = self.asked.take() else {
            return;
        };
        let Matched {
            room,
            opened,
            humans,
        } = matched;
        let bots = if opened { bots } else { 0 };
        let load = LoadPlan {
            map: room.map.clone(),
            room: room.id.clone(),
            mode: room.mode.clone(),
            bots,
            online: true,
            own: opened,
            tool: None,
        };
        if opened {
            self.front.search.status = format!(
                "NOBODY IS PLAYING THAT - OPENED A ROOM ON {}{}",
                front::map_title(&room.map),
                bots_label(bots)
            );
            self.front.search.found = Some(Found::Open { map: room.map });
        } else {
            self.front.search.status = format!(
                "{}  /  {}  /  {} PLAYING",
                front::map_title(&room.map),
                self.front.mode_name(&room.mode),
                humans.max(1)
            );
            self.front.search.found = Some(Found::Room(room));
        }
        self.found = Some((Instant::now() + FOUND_BEAT, load));
    }

    /// A server with no matchmaking: join the best listed room, or open one.
    fn listed(&mut self, rooms: Vec<RoomEntry>) {
        let Some((maps, mode, bots)) = self.asked.take() else {
            return;
        };
        // This rule takes one map; with several picked, the first is it.
        let map = maps.first().cloned();
        self.front.rooms = rooms;
        self.last_rooms = Instant::now();
        let chosen =
            front::choose_room(&self.front.rooms, map.as_deref(), mode.as_deref()).cloned();
        let load = match chosen {
            Some(room) => {
                self.front.search.status = format!(
                    "{}  /  {}  /  {} PLAYING",
                    front::map_title(&room.map),
                    self.front.mode_name(&room.mode),
                    room.players
                );
                let load = LoadPlan {
                    map: room.map.clone(),
                    room: room.id.clone(),
                    mode: room.mode.clone(),
                    bots: 0,
                    online: true,
                    own: false,
                    tool: None,
                };
                self.front.search.found = Some(Found::Room(room));
                load
            }
            None => {
                // The clock is all the entropy this wants: which map a new
                // room opens on when the player said any.
                let seed = Instant::now()
                    .duration_since(self.last_frame)
                    .subsec_nanos() as u64
                    + self.ticket;
                let Some(map) = map.or_else(|| self.front.any_map(seed)) else {
                    self.front.home_with("THE SERVER LISTED NO MAPS", true);
                    return;
                };
                self.front.search.status = format!(
                    "NO OPEN ROOM MATCHES - OPENING ONE ON {}{}",
                    front::map_title(&map),
                    bots_label(bots)
                );
                self.front.search.found = Some(Found::Open { map: map.clone() });
                LoadPlan {
                    map,
                    room: String::new(),
                    mode: mode.unwrap_or_default(),
                    bots,
                    online: true,
                    own: true,
                    tool: None,
                }
            }
        };
        self.found = Some((Instant::now() + FOUND_BEAT, load));
    }

    // -- loading ----------------------------------------------------------------

    fn start_load(&mut self, load: LoadPlan) {
        let ticket = self.next_ticket();
        let mode = if load.mode.is_empty() {
            String::new()
        } else {
            self.front.mode_name(&load.mode)
        };
        let room = match (load.online, load.room.is_empty()) {
            (false, _) => match load.tool {
                Some(ToolPlan::Drill(_)) => "AIM DRILL".to_string(),
                Some(ToolPlan::Bench) => "BENCHMARK".to_string(),
                Some(ToolPlan::Edit) => "MAP EDITOR - A DRAFT".to_string(),
                Some(ToolPlan::Viewer) => "MODEL VIEWER".to_string(),
                None => "PRACTICE - NO SERVER".to_string(),
            },
            (true, true) => "A NEW ROOM".to_string(),
            (true, false) => format!("ROOM {}", short_room(&load.room)),
        };
        let steps: &[&str] = if load.online {
            &ONLINE_STEPS
        } else {
            &OFFLINE_STEPS
        };
        self.front.begin_loading(&load.map, &mode, &room, steps);
        let (server, callsign, tx) = (self.server.clone(), self.callsign.clone(), self.tx.clone());
        let token = self.token.clone();
        spawn("hassault-load", move || {
            load_match(&server, &callsign, token.as_deref(), load, ticket, &tx)
        });
    }

    /// Hand the window to a match.
    fn start_match(&mut self, loaded: Loaded, event_loop: &ActiveEventLoop) {
        let (Some(window), Some(tables)) = (self.window.clone(), self.tables.clone()) else {
            return;
        };
        let Loaded {
            world,
            mesh,
            plan,
            socket,
            load,
            draft,
            catalog,
        } = loaded;
        // One surface per window: the menu lets go before the match takes it.
        self.gpu = None;
        let online = socket.is_some();
        let mut app = App::new(
            world,
            mesh,
            socket,
            self.settings,
            self.writer.share(),
            tables.weapons,
            tables.tacticals,
            tables.throw_physics,
            tables.skins,
            tables.hitbox,
            tables.items,
            tables.definitions,
            plan,
        );
        app.controls = self.controls.clone();
        app.set_hosted();
        // Not in the editor: a loadout puts a player on the range, and the
        // designer has a camera and no body — the rifle and the health bar
        // would be drawn over a map nobody is standing in.
        // Nor in the viewer, for the opposite reason: a loadout is three
        // weapons, and the viewer is there to hold all of them.
        let editing = matches!(load.tool, Some(ToolPlan::Edit | ToolPlan::Viewer));
        if !editing && !self.front.loadout.choice.is_null() {
            app.set_loadout(self.front.loadout.choice.clone());
        }
        app.set_loadout_menu(tables.agents, self.front.loadout.agent_choice.clone());
        if online && load.own {
            // A room this join may have opened: ours to tune, and to fill. If
            // somebody opened one on the same map first, the server refuses
            // the bots and says so — which is the right answer there.
            app.set_own_room(true);
            app.queue_bots(load.bots, "normal".to_string());
        }
        let [net_graph, hitboxes, own_tracers] = self.front.overlays;
        app.set_dev_overlays(net_graph, hitboxes, own_tracers);
        match load.tool {
            Some(ToolPlan::Drill(id)) if !id.is_empty() => {
                // The one fetch on this thread: the drill is laid out around
                // where the range put the player, which is only known now. It
                // is one small document from a server that answered a moment
                // ago, behind the loading screen's last line.
                match NodeApi::new(&self.server).drill(&id, &load.map, app.offline_position()) {
                    Ok(spec) => app.set_drill(spec),
                    Err(e) => eprintln!("hassault: no drill {id} ({e}); free range instead"),
                }
            }
            Some(ToolPlan::Bench) => {
                if let Some(path) = bench::path_for(&load.map) {
                    let tier = self.settings.video.quality.label().to_lowercase();
                    app.start_bench(BenchRun::new(
                        &load.map,
                        path,
                        &tier,
                        bench::warmup_seconds(),
                    ));
                }
            }
            Some(ToolPlan::Edit) => {
                if let Some((draft, owners)) = draft {
                    app.enter_edit_mode(&self.server, draft, owners);
                }
            }
            Some(ToolPlan::Viewer) => app.start_viewer(catalog),
            _ => {}
        }
        app.attach(window, event_loop);
        self.stage = Stage::Match(Box::new(app));
    }

    /// A hosted match is over: take the window back.
    fn end_match(&mut self, why: Ended, event_loop: &ActiveEventLoop) {
        let Stage::Match(mut app) = std::mem::replace(&mut self.stage, Stage::Front) else {
            return;
        };
        let benched = app.take_bench();
        // What the pause menu changed carries into the next match.
        let (settings, choice, agents) = app.hand_back();
        self.settings = settings;
        self.controls = app.controls.clone();
        self.front.loadout.choice = choice;
        self.front.loadout.agent_choice = agents;
        // The renderer, its surface and the socket all go here.
        drop(app);

        let Some(window) = self.window.clone() else {
            return;
        };
        let _ = window.set_cursor_grab(CursorGrabMode::None);
        window.set_cursor_visible(true);
        window.set_title("HorribleAssault");
        match pollster::block_on(FrontGpu::new(window, &self.settings.gpu.choice())) {
            Ok(mut gpu) => {
                // A new device: the pictures go up again.
                if let Some(atlas) = &self.atlas {
                    gpu.set_art(atlas);
                }
                self.gpu = Some(gpu)
            }
            Err(e) => {
                eprintln!("hassault: {e}");
                event_loop.exit();
                return;
            }
        }
        // A new device has an empty glyph atlas: send all of it.
        self.kit.mark_all_dirty();
        match why {
            Ended::Left => self.front.show(Screen::Home),
            Ended::Refused(why) => self
                .front
                .home_with(format!("THE SERVER ENDED THE MATCH - {why}"), true),
            Ended::Closed(why) => self
                .front
                .home_with(format!("THE CONNECTION CLOSED - {why}"), true),
        }
        if let Some(line) = benched {
            self.front.home_with(bench_summary(&line), false);
        }
        self.last_frame = Instant::now();
        self.refresh_rooms();
    }

    // -- the channel ------------------------------------------------------------

    fn pump(&mut self) {
        while let Ok((ticket, msg)) = self.rx.try_recv() {
            if ticket != 0 && ticket != self.ticket {
                // From a job that was cancelled or replaced. A socket in here
                // closes as it is dropped.
                continue;
            }
            match msg {
                Msg::Step(index, state, note) => self.front.step(index, state, note),
                Msg::Booted(booted) => self.booted(*booted),
                Msg::BootFailed(why) => self.front.boot_failed = Some(why),
                Msg::Rooms(Ok(rooms)) => {
                    self.front.rooms = rooms;
                    self.front.rooms_error = None;
                    if self.front.room >= self.front.rooms.len() {
                        self.front.room = 0;
                    }
                    // The roster rides the list: every refresh of one, while
                    // the server view is up, is a refresh of the other.
                    let watched = self.front.rooms.get(self.front.room).map(|r| r.id.clone());
                    match watched {
                        Some(room) if self.watching_server() => self.fetch_roster(room),
                        Some(_) => {}
                        None => self.front.roster = None,
                    }
                }
                Msg::Roster(detail) => self.front.roster = detail,
                Msg::Art(atlas) => {
                    if let Some(gpu) = &mut self.gpu {
                        gpu.set_art(&atlas);
                    }
                    self.front.art = map_art::names()
                        .into_iter()
                        .filter(|name| atlas.has(name))
                        .collect();
                    self.atlas = Some(atlas);
                }
                Msg::Lobby(Ok((token, view))) => self.lobby_seen(token, view),
                Msg::Lobby(Err(why)) => self.front.home_with(why.to_uppercase(), true),
                Msg::Providers(providers) => self.front.providers = providers,
                Msg::Account(Some(account)) => {
                    self.front.account = Some(account_view(&account));
                }
                Msg::Account(None) => self.sign_out(None),
                Msg::SignInStep {
                    provider,
                    url,
                    code,
                } => {
                    // Only if it is still wanted: cancel may have got here first.
                    if matches!(self.front.signin, SignIn::Starting(_)) {
                        // Shown as well as opened: an open that fails does so
                        // silently, and the address on screen is the way round.
                        open_url(&url);
                        self.front.signin = SignIn::Waiting {
                            provider,
                            url,
                            code,
                        };
                    }
                }
                Msg::SignedIn(token, account) => self.signed_in(token, account),
                Msg::SignInFailed(why) => {
                    if !matches!(self.front.signin, SignIn::Idle) {
                        self.front.signin = SignIn::Failed(why);
                    }
                }
                Msg::AccountData(data) => {
                    let AccountData {
                        items,
                        wallet,
                        listings,
                        note,
                    } = *data;
                    self.front.items = items;
                    self.front.wallet = wallet;
                    self.front.listings = listings;
                    if self.front.listing >= self.front.listings.len() {
                        self.front.listing = 0;
                    }
                    match note {
                        Ok(Some(said)) => self.front.home_with(said, false),
                        Ok(None) => {}
                        Err(why) => self.front.home_with(why.to_uppercase(), true),
                    }
                }
                Msg::SignedOut(why) => self.sign_out(Some(why)),
                Msg::LobbyGone => {
                    if self.lobby.take().is_some() {
                        self.front.lobby = None;
                        self.front.home_with("THE LOBBY CLOSED", true);
                    }
                }
                Msg::Rooms(Err(why)) => self.front.rooms_error = Some(why),
                Msg::Searched(rooms) => self.searched(rooms),
                Msg::Plan(plan, size) => {
                    self.front.loading.plan = plan;
                    self.front.loading.plan_size = size;
                }
                Msg::Loaded(loaded) => {
                    let last = self.front.steps.len().saturating_sub(1);
                    self.front.step(last, StepState::Running, "");
                    // Two frames, so the screen shows this line before the
                    // thread goes quiet starting the renderer.
                    self.ready = Some((loaded, 2));
                }
                Msg::LoadFailed(why) => self.front.home_with(why.to_uppercase(), true),
            }
        }
    }

    // -- input ------------------------------------------------------------------

    fn carry_out(&mut self, intent: Option<Intent>, event_loop: &ActiveEventLoop) {
        let Some(intent) = intent else { return };
        match intent {
            Intent::FindMatch { maps, mode, bots } => self.start_search(maps, mode, bots),
            Intent::SignIn(provider) => self.sign_in(provider),
            Intent::SignInCancel => {
                self.signin_gen.fetch_add(1, Ordering::SeqCst);
            }
            Intent::SignOut => self.sign_out(None),
            Intent::AccountRefresh => self.account_job(AccountJob::Refresh),
            Intent::Equip(id) => self.account_job(AccountJob::Equip(id)),
            Intent::Unequip(weapon) => self.account_job(AccountJob::Unequip(weapon)),
            Intent::BuyKey => self.account_job(AccountJob::BuyKey),
            Intent::OpenCase(id) => self.account_job(AccountJob::OpenCase(id)),
            Intent::BuyListing(id) => self.account_job(AccountJob::BuyListing(id)),
            Intent::LobbyCreate => self.lobby_create(),
            Intent::LobbyJoin(code) => self.lobby_join(code),
            Intent::LobbyLeave => self.lobby_leave(),
            Intent::LobbyPlay { maps, mode } => self.lobby_play(maps, mode),
            Intent::Join { room, map, mode } => self.start_load(LoadPlan {
                map,
                room,
                mode,
                bots: 0,
                online: true,
                own: false,
                tool: None,
            }),
            Intent::Tool { tool, map, drill } => self.start_load(LoadPlan {
                map,
                room: String::new(),
                mode: String::new(),
                bots: 0,
                online: false,
                own: false,
                tool: match (tool, drill) {
                    (DevTool::Drill, Some(id)) => Some(ToolPlan::Drill(id)),
                    (DevTool::Bench, _) => Some(ToolPlan::Bench),
                    (DevTool::Editor, _) => Some(ToolPlan::Edit),
                    (DevTool::Viewer, _) => Some(ToolPlan::Viewer),
                    // A drill with no drill named is the free range; the
                    // server view joins a room and never asks for a tool.
                    (DevTool::Drill, None) | (DevTool::Server, _) => None,
                },
            }),
            Intent::Practice { map } => self.start_load(LoadPlan {
                map,
                room: String::new(),
                mode: String::new(),
                bots: 0,
                online: false,
                own: false,
                tool: None,
            }),
            Intent::CancelSearch => {
                self.next_ticket();
                self.asked = None;
                self.front.show(Screen::Home);
            }
            Intent::RefreshRooms => self.refresh_rooms(),
            Intent::Roster(room) => self.fetch_roster(room),
            Intent::Save(key, raw) => self.writer.save(key, serde_json::Value::String(raw)),
            Intent::SaveCallsign(name) => {
                self.writer.save(
                    settings::KEY_CALLSIGN,
                    serde_json::Value::String(name.clone()),
                );
                self.callsign = name;
            }
            Intent::OpenSettings => {
                if !self.menu.open {
                    self.menu.toggle();
                }
            }
            Intent::Retry => self.start_boot(),
            Intent::Quit => event_loop.exit(),
        }
    }

    fn menu_rows(&self) -> usize {
        self.menu.rows(&self.settings, &self.controls, false).len()
    }

    fn save_controls(&mut self) {
        self.writer.save(
            settings::KEY_CONTROLS,
            serde_json::Value::String(self.controls.serialize()),
        );
    }

    /// The settings pages' keyboard: `App::menu_key`, without a match behind it.
    fn menu_key(&mut self, code: KeyCode) {
        if let Some(action) = self.menu.listening {
            let name = hassault_native::controls::code_name(code);
            match code {
                KeyCode::Escape => {
                    self.menu.listening = None;
                    return;
                }
                KeyCode::Backspace => self.controls.bind(action, None),
                _ if hassault_native::controls::RESERVED.contains(&name.as_str()) => return,
                _ => self.controls.bind(action, Some(&name)),
            }
            self.menu.listening = None;
            self.save_controls();
            return;
        }
        let count = self.menu_rows();
        match code {
            KeyCode::Escape => {
                self.menu.escape();
            }
            KeyCode::ArrowUp | KeyCode::KeyW => self.menu.move_cursor(-1, count),
            KeyCode::ArrowDown | KeyCode::KeyS => self.menu.move_cursor(1, count),
            KeyCode::ArrowLeft | KeyCode::KeyA => self.menu_activate(self.menu.cursor(), -1),
            KeyCode::ArrowRight
            | KeyCode::KeyD
            | KeyCode::Enter
            | KeyCode::NumpadEnter
            | KeyCode::Space => self.menu_activate(self.menu.cursor(), 1),
            _ => {}
        }
    }

    /// Act on one settings row. The rows a match adds — leave, loadout, store —
    /// are not on these pages (`Menu::front`), so they have no arm here.
    fn menu_activate(&mut self, index: usize, step: i32) {
        let rows = self.menu.rows(&self.settings, &self.controls, false);
        let Some(row) = rows.get(index) else { return };
        match row.action {
            Action::Bind(action) => self.menu.listening = Some(action),
            Action::ResetKeys => {
                self.controls = Controls::default();
                self.save_controls();
            }
            Action::Resume => self.menu.close(),
            Action::Open(page) => {
                self.menu.page = page;
                self.menu.move_cursor(-(self.menu.cursor() as i32), 1);
            }
            Action::Back => self.menu.back(),
            Action::CrosshairPaste | Action::CrosshairCopy => {
                self.menu.note = Some((
                    "SHARE CODES ARE COPIED AND PASTED FROM THE MENU IN A MATCH".to_string(),
                    false,
                ));
            }
            Action::Restart => {
                // Every pending write lands first: the new process reads the
                // GPU choice back from the file.
                self.writer.finish();
                hassault_native::gpu::restart();
            }
            action => {
                let keys = menu::apply(action, step, &mut self.settings);
                if matches!(
                    action,
                    Action::Fullscreen | Action::Monitor | Action::ExclusiveMode
                ) {
                    self.apply_fullscreen();
                }
                for key in keys {
                    if let Some(value) = self.settings.value_for(key) {
                        self.writer.save(key, value);
                    }
                }
            }
        }
    }

    /// The display rows act on the window at once; everything else on these
    /// pages is read when the next match starts.
    fn apply_fullscreen(&mut self) {
        let Some(window) = &self.window else { return };
        let video = self.settings.video;
        let monitors: Vec<_> = window.available_monitors().collect();
        let current = window.current_monitor();
        hassault_native::display::capture(&video, monitors.clone(), current.clone());
        window.set_fullscreen(hassault_native::display::fullscreen(
            &video, monitors, current,
        ));
    }

    fn front_event(&mut self, event_loop: &ActiveEventLoop, event: WindowEvent) {
        match event {
            WindowEvent::CloseRequested => event_loop.exit(),
            WindowEvent::Resized(size) => {
                if let Some(gpu) = &mut self.gpu {
                    gpu.resize(size.width, size.height);
                }
            }
            // winit does not promise a `Resized` alongside this, and the
            // surface is sized in physical pixels.
            WindowEvent::ScaleFactorChanged { .. } => {
                if let (Some(gpu), Some(window)) = (&mut self.gpu, &self.window) {
                    let size = window.inner_size();
                    gpu.resize(size.width, size.height);
                }
            }
            WindowEvent::CursorMoved { position, .. } => {
                let (x, y) = (position.x as f32, position.y as f32);
                self.front.pointer = (x, y);
                if self.menu.open {
                    let (w, h) = self.size();
                    let count = self.menu_rows();
                    self.menu.hover(x, y, count, w, h);
                }
            }
            WindowEvent::MouseInput {
                state: ElementState::Pressed,
                button: MouseButton::Left,
                ..
            } => {
                let (w, h) = self.size();
                if self.menu.open {
                    let count = self.menu_rows();
                    if let Some(index) = self.menu.hit(count, w, h) {
                        self.menu_activate(index, 1);
                    }
                } else {
                    let intent = self.front.click(Some(&self.kit), w, h);
                    self.carry_out(intent, event_loop);
                }
            }
            WindowEvent::KeyboardInput { event, .. } if event.state == ElementState::Pressed => {
                let PhysicalKey::Code(code) = event.physical_key else {
                    return;
                };
                if self.menu.open {
                    self.menu_key(code);
                    return;
                }
                // Text goes to a field that is open; the key then goes to the
                // screen, which while editing only acts on Enter, Escape and
                // Backspace — so a typed digit is never also a tab shortcut.
                if let Some(text) = event.text.as_deref() {
                    self.front.text(text);
                }
                let intent = self.front.key(code);
                self.carry_out(intent, event_loop);
            }
            WindowEvent::RedrawRequested => self.draw(event_loop),
            _ => {}
        }
    }

    fn draw(&mut self, event_loop: &ActiveEventLoop) {
        let (w, h) = self.size();
        let Some(gpu) = self.gpu.as_mut() else { return };
        self.kit.begin_frame();
        self.overlay.clear();
        self.front.paint(Some(&self.kit), w, h, &mut self.overlay);
        self.menu.build(
            &self.settings,
            &self.controls,
            false,
            w,
            h,
            Some(&self.kit),
            &mut self.overlay,
        );
        // The pictures under it all, once they have loaded: the backdrop and
        // a tile per map, cut from the one atlas.
        self.art_verts.clear();
        if let Some(atlas) = &self.atlas {
            for quad in self.front.images(Some(&self.kit), w, h) {
                atlas.quad(
                    &quad.map,
                    &quad.rect,
                    quad.tint,
                    (w, h),
                    &mut self.art_verts,
                );
            }
        }
        let upload = self.kit.take_upload();
        match gpu.draw(&self.overlay, upload.as_ref(), &self.art_verts) {
            Ok(true) => {}
            // Minimised or covered: nothing was presented, so nothing paced
            // this pass of the loop. Sleep rather than spin a core on it.
            Ok(false) => thread::sleep(Duration::from_millis(16)),
            Err(e) => {
                eprintln!("hassault: {e}");
                event_loop.exit();
            }
        }
    }

    /// Take the window back if the match just ended.
    fn after_match(&mut self, event_loop: &ActiveEventLoop) {
        let ended = match &mut self.stage {
            Stage::Match(app) => app.take_ended(),
            Stage::Front => None,
        };
        if let Some(why) = ended {
            self.end_match(why, event_loop);
        }
    }
}

impl ApplicationHandler for Shell {
    fn resumed(&mut self, event_loop: &ActiveEventLoop) {
        if self.window.is_some() {
            return;
        }
        // Fullscreen from the first frame, as a direct launch opens: see
        // `App::resumed`.
        let monitors: Vec<_> = event_loop.available_monitors().collect();
        let current = event_loop.primary_monitor();
        hassault_native::display::capture(&self.settings.video, monitors.clone(), current.clone());
        let fullscreen =
            hassault_native::display::fullscreen(&self.settings.video, monitors, current);
        let attrs = Window::default_attributes()
            .with_title("HorribleAssault")
            .with_fullscreen(fullscreen)
            .with_inner_size(winit::dpi::LogicalSize::new(1280.0, 800.0));
        let window = match event_loop.create_window(attrs) {
            Ok(window) => Arc::new(window),
            Err(e) => {
                eprintln!("hassault: could not open a window: {e}");
                event_loop.exit();
                return;
            }
        };
        match pollster::block_on(FrontGpu::new(window.clone(), &self.settings.gpu.choice())) {
            Ok(gpu) => {
                eprintln!("hassault: menu on {}", gpu.adapter_name);
                self.gpu = Some(gpu);
            }
            Err(e) => {
                eprintln!("hassault: {e}");
                event_loop.exit();
                return;
            }
        }
        self.window = Some(window.clone());
        self.last_frame = Instant::now();
        // Nine pictures to decode: not on this thread, and not waited for.
        // The menu is up without them and gains them when they land.
        let tx = self.tx.clone();
        spawn("hassault-art", move || {
            let _ = tx.send((0, Msg::Art(Arc::new(Atlas::build()))));
        });
        self.start_boot();
        window.request_redraw();
        event_loop.set_control_flow(ControlFlow::Poll);
    }

    fn window_event(&mut self, event_loop: &ActiveEventLoop, id: WindowId, event: WindowEvent) {
        if let Stage::Match(app) = &mut self.stage {
            app.window_event(event_loop, id, event);
            self.after_match(event_loop);
            return;
        }
        self.front_event(event_loop, event);
    }

    fn device_event(&mut self, event_loop: &ActiveEventLoop, id: DeviceId, event: DeviceEvent) {
        if let Stage::Match(app) = &mut self.stage {
            app.device_event(event_loop, id, event);
        }
    }

    fn about_to_wait(&mut self, event_loop: &ActiveEventLoop) {
        self.poll_lobby();
        if let Stage::Match(app) = &mut self.stage {
            app.about_to_wait(event_loop);
            self.after_match(event_loop);
            return;
        }
        let now = Instant::now();
        self.front
            .tick(now.duration_since(self.last_frame).as_secs_f32());
        self.last_frame = now;
        self.pump();

        if self.found.as_ref().is_some_and(|(at, _)| now >= *at) {
            if let Some((_, load)) = self.found.take() {
                self.start_load(load);
            }
        }
        match self.ready.take() {
            Some((loaded, 0)) => {
                self.start_match(*loaded, event_loop);
                return;
            }
            Some((loaded, frames)) => self.ready = Some((loaded, frames - 1)),
            None => {}
        }
        if self.front.screen == Screen::Home && self.last_rooms.elapsed() >= ROOMS_EVERY {
            self.refresh_rooms();
        }
        if let Some(window) = &self.window {
            window.request_redraw();
        }
    }
}

/// Pause a load once the world is built, for `HASSAULT_LOAD_HOLD_MS`.
///
/// Against a server on the same machine a load is over in a few hundred
/// milliseconds, which is too fast to photograph and far too fast to judge a
/// layout by. Unset — always, outside somebody looking at this screen on
/// purpose — it does nothing.
fn hold_for_a_look() {
    let hold = std::env::var("HASSAULT_LOAD_HOLD_MS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(0);
    if hold > 0 {
        thread::sleep(Duration::from_millis(hold.min(60_000)));
    }
}

fn spawn(name: &str, job: impl FnOnce() + Send + 'static) {
    if let Err(e) = thread::Builder::new().name(name.into()).spawn(job) {
        eprintln!("hassault: could not start {name}: {e}");
    }
}

/// Fetch what the menu and every match need.
///
/// Two of these are required and the rest are not, for the reason `main.rs`
/// gives for each: with no weapons nothing fires, and with no maps there is
/// nowhere to go — but a server that cannot list operators still has a game.
fn boot(server: &str, ticket: u64, tx: &Sender<(u64, Msg)>) {
    let node = NodeApi::new(server);
    let say = |index: usize, state: StepState, note: String| {
        let _ = tx.send((ticket, Msg::Step(index, state, note)));
    };
    let fail = |index: usize, why: String| {
        say(index, StepState::Failed, String::new());
        let _ = tx.send((ticket, Msg::BootFailed(why)));
    };

    say(0, StepState::Running, String::new());
    let weapons = match node.weapons() {
        Ok(weapons) if !weapons.is_empty() => weapons,
        Ok(_) => return fail(0, "the server listed no weapons".into()),
        Err(e) => return fail(0, e.to_string()),
    };
    say(0, StepState::Done, weapons.len().to_string());

    say(1, StepState::Running, String::new());
    let maps: Vec<MapEntry> = match node.maps() {
        Ok(maps) if !maps.is_empty() => maps
            .into_iter()
            .map(|m| MapEntry {
                name: m.name,
                size: m.size,
            })
            .collect(),
        Ok(_) => return fail(1, "the server listed no maps".into()),
        Err(e) => return fail(1, e.to_string()),
    };
    // A duel is paired by the server and rated, so it is not a game a guest
    // picks off a list.
    let modes: Vec<ModeEntry> = node
        .modes()
        .unwrap_or_default()
        .into_iter()
        .filter(|(id, _)| id != "duel")
        .map(|(id, name)| ModeEntry { id, name })
        .collect();
    say(
        1,
        StepState::Done,
        format!("{} MAPS  /  {} GAMES", maps.len(), modes.len()),
    );

    say(2, StepState::Running, String::new());
    let tacticals = node.tacticals().unwrap_or_default();
    let throw_physics = node.throw_physics().ok();
    let items = node.items().unwrap_or_default();
    let hitbox = node.hitbox().unwrap_or_default();
    say(
        2,
        if tacticals.is_empty() {
            StepState::Skipped
        } else {
            StepState::Done
        },
        format!("{} GRENADES", tacticals.len()),
    );

    say(3, StepState::Running, String::new());
    let agents = node.agents().unwrap_or_default();
    let skins = node
        .skins()
        .map(|items| viewmodel::equipped_skins(&items))
        .unwrap_or_default();
    say(
        3,
        if agents.is_empty() {
            StepState::Skipped
        } else {
            StepState::Done
        },
        format!("{} OPERATORS", agents.len()),
    );

    say(4, StepState::Running, String::new());
    let (definitions, state) = match node.console_definitions() {
        Ok(defs) => (defs, StepState::Done),
        Err(_) => (Definitions::default(), StepState::Skipped),
    };
    say(
        4,
        state,
        format!(
            "{} COMMANDS  /  {} CVARS",
            definitions.commands.len(),
            definitions.cvars.len()
        ),
    );

    say(5, StepState::Running, String::new());
    let (rooms, state) = match node.rooms() {
        Ok(rooms) => (rooms, StepState::Done),
        Err(_) => (Vec::new(), StepState::Skipped),
    };
    // For DEV TOOLS. A server that lists none leaves the drill unavailable
    // there, and says so; it is not a reason the menu cannot open.
    let drills = node.drills().unwrap_or_default();
    say(5, state, format!("{} OPEN", rooms.len()));

    let _ = tx.send((
        ticket,
        Msg::Booted(Box::new(Booted {
            tables: Tables {
                weapons,
                tacticals,
                throw_physics,
                items,
                hitbox,
                skins,
                definitions,
                agents,
            },
            maps,
            modes,
            rooms,
            drills,
        })),
    ));
}

/// Fetch a map, build it, and — online — connect and be seated.
///
/// Everything a direct launch does before it opens a window (`main.rs`), in
/// the same order, reporting each phase as it goes.
fn load_match(
    server: &str,
    callsign: &str,
    token: Option<&str>,
    load: LoadPlan,
    ticket: u64,
    tx: &Sender<(u64, Msg)>,
) {
    let node = NodeApi::new(server);
    let say = |index: usize, state: StepState, note: String| {
        let _ = tx.send((ticket, Msg::Step(index, state, note)));
    };
    let fail = |index: usize, why: String| {
        say(index, StepState::Failed, String::new());
        let _ = tx.send((ticket, Msg::LoadFailed(why)));
    };

    say(0, StepState::Running, String::new());
    // The editor works on a draft, which the server serves as a map named
    // `draft:<id>` — so from here on it is the ordinary load.
    let mut opened: Option<DraftInfo> = None;
    let map_name = if matches!(load.tool, Some(ToolPlan::Edit)) {
        match node.create_draft(&load.map) {
            Ok(draft) => {
                let name = draft.map_name.clone();
                opened = Some(draft);
                name
            }
            Err(e) if e.is_missing() => {
                return fail(
                    0,
                    "this server keeps no map drafts - the editor needs a game server \
                     run with HASSAULT_ENABLE_DRAFTS=1"
                        .into(),
                )
            }
            Err(e) => return fail(0, format!("could not open a draft of {} - {e}", load.map)),
        }
    } else {
        load.map.clone()
    };
    let info = match node.map_info(&map_name) {
        Ok(info) => info,
        Err(e) => return fail(0, format!("could not fetch {} - {e}", load.map)),
    };
    let cubic = info.cubic_size;
    let expected = info.cubic_size * info.plane_order.len();
    let cubes = match node.map_cubes(&map_name, expected) {
        Ok(cubes) => cubes,
        Err(e) => return fail(0, format!("could not fetch {} - {e}", load.map)),
    };
    say(
        0,
        StepState::Done,
        format!("{} KB", cubes.len().div_ceil(1024)),
    );

    say(1, StepState::Running, String::new());
    let ssize = info.ssize;
    let world = match World::new(info, &cubes) {
        Ok(world) => world,
        Err(e) => return fail(1, format!("{} would not build - {e}", load.map)),
    };
    let mesh = geometry::build_world_mesh(&world);
    let plan = radar::floor_plan(&world);
    say(1, StepState::Done, format!("{ssize} X {ssize} GRID"));
    let _ = tx.send((ticket, Msg::Plan(plan.clone(), ssize as f32)));
    hold_for_a_look();

    let socket = if load.online {
        say(2, StepState::Running, host_label(server).to_uppercase());
        // Signed in, the seat is the account's: its name, its skins, its
        // rating. A guest is a callsign.
        let address = match token {
            Some(token) => node.account_ws_url(token),
            None => node.guest_ws_url(callsign),
        };
        let socket = match MatchSocket::connect(&address) {
            Ok(socket) => socket,
            Err(e) => return fail(2, format!("could not connect - {e}")),
        };
        say(2, StepState::Done, String::new());

        say(3, StepState::Running, String::new());
        if let Err(e) = socket.join(&load.map, &load.room, "", callsign, false, &load.mode) {
            return fail(3, format!("could not ask to join - {e}"));
        }
        match socket.await_welcome(JOIN_PATIENCE) {
            Ok(seated) => say(
                3,
                StepState::Done,
                format!(
                    "ROOM {}  /  {} IN IT",
                    short_room(&seated.room),
                    seated.players
                ),
            ),
            Err(why) => return fail(3, format!("the server refused the join - {why}")),
        }
        Some(socket)
    } else {
        None
    };

    // Which brush painted each cell: what turns a crosshair on a wall into a
    // brush to drag. Without it the camera still flies; only picking is off.
    let draft = opened.map(|draft| {
        let owners = node
            .draft_owners(&draft.id, cubic)
            .ok()
            .and_then(|bytes| hassault_native::editor::decode_owners(&bytes, cubic))
            .unwrap_or_default();
        (draft, owners)
    });

    // The viewer's finishes. A server that lists none leaves every weapon
    // on its stock finish, and the card says so.
    let catalog = if matches!(load.tool, Some(ToolPlan::Viewer)) {
        node.skin_catalog().unwrap_or_default()
    } else {
        Vec::new()
    };

    let _ = tx.send((
        ticket,
        Msg::Loaded(Box::new(Loaded {
            world,
            mesh,
            plan,
            socket,
            load,
            draft,
            catalog,
        })),
    ));
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_server_address_is_shown_as_its_host() {
        assert_eq!(
            host_label("https://horrible-games.fly.dev/"),
            "horrible-games.fly.dev"
        );
        assert_eq!(host_label("http://127.0.0.1:9200"), "127.0.0.1:9200");
    }

    #[test]
    fn the_servers_answer_to_a_search_reads_as_a_room() {
        // The shape `/api/hassault/matchmake` answers in: a listed room, flat,
        // with what the search adds beside it.
        let matched: Matched = serde_json::from_str(
            r#"{"id":"ab12cd34","map":"hd_dust2","mode":"dm","playerCount":5,
                "humans":2,"maxPlayers":16,"opened":false}"#,
        )
        .unwrap();
        assert_eq!(matched.room.id, "ab12cd34");
        assert_eq!(matched.room.players, 5);
        assert_eq!(matched.humans, 2);
        assert!(!matched.opened);
        assert_eq!(bots_label(0), "");
        assert_eq!(bots_label(1), " WITH 1 BOT");
        assert_eq!(bots_label(4), " WITH 4 BOTS");
    }

    #[test]
    fn a_benchmark_comes_back_as_one_line_of_the_menu() {
        let line = r#"{"map":"hd_dust2","client":"native","tier":"high","width":1920,
            "height":1080,"frames":4211,"seconds":20.0,"fps":210.55,"p50":4.6,"p99":7.84}"#;
        let said = bench_summary(line);
        assert_eq!(
            said,
            "BENCHMARK  DUST2  /  HIGH  /  1920X1080  -  211 FPS, P99 7.8 MS, 4211 FRAMES"
        );
        for ch in said.chars() {
            assert!(hassault_native::hud::has_glyph(ch), "no glyph for {ch:?}");
        }
        // A line that is not a result still reads as a sentence.
        assert!(bench_summary("").starts_with("BENCHMARK"));
    }

    #[test]
    fn only_a_web_address_is_handed_to_the_browser() {
        assert!(is_web_url(
            "https://horrible-games.fly.dev/auth/github/login?lid=ab12"
        ));
        assert!(is_web_url("http://127.0.0.1:9200/auth/google/login?lid=x"));
        for bad in [
            "",
            "file:///C:/Windows/System32/calc.exe",
            "javascript:alert(1)",
            "calc.exe",
            "https://ok.example/ & calc.exe",
            "https://ok.example/\"quoted\"",
            "https://ok.example/\nsecond-line",
        ] {
            assert!(!is_web_url(bad), "{bad:?} would have been opened");
        }
    }

    #[test]
    fn an_account_is_shown_by_its_name_and_a_blank_handle_is_none() {
        let view = account_view(&Account {
            id: "acct-1".into(),
            display_name: "Sable".into(),
            handle: Some(String::new()),
        });
        assert_eq!(view.name, "Sable");
        assert_eq!(view.handle, None);
        // No display name: the id, rather than nothing.
        assert_eq!(
            account_view(&Account {
                id: "acct-1".into(),
                ..Default::default()
            })
            .name,
            "acct-1"
        );
    }

    #[test]
    fn a_room_id_is_shown_short() {
        assert_eq!(short_room("3d8081a0-77c1-4e2b"), "3D8081A0");
        assert_eq!(short_room("ab"), "AB");
    }

    #[test]
    fn the_last_loading_phase_is_the_renderer_in_both_lists() {
        // `pump` marks the last step running when the load lands, whichever
        // list is up.
        assert_eq!(ONLINE_STEPS.last(), OFFLINE_STEPS.last());
    }
}

//! The socket, and the thread that owns it.
//!
//! **One thread owns the socket outright. Nothing else touches it, in either
//! direction.** The game loop hands outbound messages to a channel and reads
//! inbound ones off another; it never blocks on I/O and never takes a lock.
//!
//! That is a correction, and an instructive one. The first version shared the
//! socket behind a `Mutex`: the reader locked it, called `read()`, and the render
//! loop locked it to write. The comment justifying it said a write "waits at most
//! one inbound frame, and at 20Hz snapshots that is a fraction of a frame" —
//! which is exactly backwards. One inbound frame at 20 Hz is **50 ms**, and
//! `read()` blocks *while holding the lock*, so every frame's input send waited up
//! to a full snapshot interval. The result was a client pinned near 20–36 fps on
//! an RTX 4080 drawing 13,000 triangles, with the frame time going nowhere near
//! the GPU. A renderer built for latency, throttled by its own network lock.
//!
//! The fix is not a better lock, it is no lock: give the socket a single owner and
//! let it do both jobs. The loop it runs is
//!
//! 1. read with a short timeout,
//! 2. drain and send whatever the game queued,
//!
//! so an outbound message waits at most `READ_TIMEOUT`, and the render thread
//! waits for nothing at all.
//!
//! Input is **batched** — one message carrying every command since the last send,
//! rather than one per frame. At 240fps that matters rather more than it did at
//! 60, and the server's parser caps a batch at 64 anyway.

use std::net::TcpStream;
use std::sync::mpsc::{self, Receiver, Sender, TryRecvError};
use std::thread;
use std::time::{Duration, Instant};

use tungstenite::stream::MaybeTlsStream;
use tungstenite::{Message, WebSocket};

use crate::protocol::{self, Command, ConsoleExec, Event, InputBatch, JoinRequest, Outbound};

type Socket = WebSocket<MaybeTlsStream<TcpStream>>;

/// How long the owner thread blocks in `read` before checking the outbound queue.
///
/// This is the **upper bound on outbound latency**, so it wants to be small. It is
/// not a busy-wait: the thread is blocked in the OS the whole time, costing
/// nothing. 4 ms is under a frame even at 240fps.
const READ_TIMEOUT: Duration = Duration::from_millis(4);

#[derive(Debug)]
pub enum NetError {
    Connect(String),
    Send(String),
}

impl std::fmt::Display for NetError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            NetError::Connect(e) => write!(f, "could not open the node's socket: {e}"),
            NetError::Send(e) => write!(f, "could not send to the node: {e}"),
        }
    }
}

impl std::error::Error for NetError {}

/// What the owner thread reports upward.
pub enum Incoming {
    Event(Event),
    /// The socket closed. Terminal — the loop should stop rather than spin on a
    /// dead channel.
    Closed(String),
}

/// Minimum gap between input messages.
///
/// **Not** one message per frame, which is what this used to do. This client
/// deliberately runs without a frame cap, so "flush every frame" meant several
/// hundred WebSocket text frames a second at a player — each one a JSON parse on
/// the backend's single event loop, which is also running the 20 Hz match tick
/// for everybody in the room. The tick then slips, snapshots arrive in lumps,
/// and the prediction gets corrected in lumps: rubber-banding produced entirely
/// by the client's own send rate.
///
/// Batching costs nothing, because every command still arrives — commands carry
/// their own `dt` and sequence number, so a batch of eight is exactly as
/// simulable as eight messages of one. 33 ms is the browser client's
/// `SEND_INTERVAL_MS`; the two clients having the same send rate is worth more
/// than either number being optimal.
pub const SEND_INTERVAL: Duration = Duration::from_millis(33);

/// Commands one `input` message may carry, matching the server's
/// `MAX_COMMANDS_PER_MESSAGE`. Anything past this is discarded on arrival, so
/// the client trims first rather than sending bytes to be thrown away.
pub const MAX_COMMANDS_PER_MESSAGE: usize = 64;

/// How often to measure the round trip.
const PING_INTERVAL: Duration = Duration::from_millis(1000);

pub struct MatchSocket {
    outbound: Sender<String>,
    rx: Receiver<Incoming>,
    seq: u64,
    /// Commands accumulated since the last flush.
    pending: Vec<Command>,
    /// When the last input message went out, so `flush` can rate-limit itself
    /// rather than trusting every caller to remember to.
    last_send: Option<Instant>,
    last_ping: Option<Instant>,
    /// Whether the backlog warning has already been printed. See `flush`.
    warned_overflow: bool,
}

impl MatchSocket {
    /// Connect and hand the socket to its owner thread. Does **not** join a match
    /// — that is a separate message, because joining is what binds a player to
    /// this socket and the server wants to check the account first.
    pub fn connect(ws_url: &str) -> Result<MatchSocket, NetError> {
        let (mut socket, _response) =
            tungstenite::connect(ws_url).map_err(|e| NetError::Connect(e.to_string()))?;

        // The read timeout is what lets one thread do both jobs. Without it the
        // thread would sit in `read` until a snapshot arrived, and anything queued
        // for sending would wait there with it.
        match socket.get_mut() {
            MaybeTlsStream::Plain(stream) => stream
                .set_read_timeout(Some(READ_TIMEOUT))
                .map_err(|e| NetError::Connect(e.to_string()))?,
            // The node is on this machine, so the socket is plain TCP and this
            // arm is unreachable today. If a TLS transport is ever added, the
            // timeout has to be set on the stream underneath it — and the cost of
            // forgetting is latency, not breakage, which is exactly the kind of
            // thing that goes unnoticed. Hence the warning rather than silence.
            _ => eprintln!(
                "hassault: warning — no read timeout on this transport;                  input will be sent in bursts rather than promptly"
            ),
        }

        let (inbound_tx, rx) = mpsc::channel();
        let (outbound, outbound_rx) = mpsc::channel::<String>();

        thread::Builder::new()
            .name("hassault-ws".into())
            .spawn(move || io_loop(socket, inbound_tx, outbound_rx))
            .map_err(|e| NetError::Connect(e.to_string()))?;

        Ok(MatchSocket {
            outbound,
            rx,
            seq: 0,
            pending: Vec::new(),
            last_send: None,
            last_ping: None,
            warned_overflow: false,
        })
    }

    fn send<T: serde::Serialize>(&self, msg: &T) -> Result<(), NetError> {
        let text = serde_json::to_string(msg).map_err(|e| NetError::Send(e.to_string()))?;
        // Queued, not written. This returns immediately and can never block the
        // caller on the network — which is the whole point.
        self.outbound
            .send(text)
            .map_err(|_| NetError::Send("the connection is closed".into()))
    }

    /// Ask to join.
    ///
    /// `room`/`host` empty means "a match on this node, on this map"; `ranked`
    /// means "not on this node at all" — the game server opens it and the node
    /// proxies, because a room inside a player's own backend cannot adjudicate
    /// that player. Everything after the join is the same wire either way, which
    /// is why this is one method with a flag rather than a second client.
    #[allow(clippy::too_many_arguments)]
    pub fn join(
        &self,
        map: &str,
        room: &str,
        host: &str,
        name: &str,
        ranked: bool,
        mode: &str,
    ) -> Result<(), NetError> {
        self.send(&Outbound::new(
            "join",
            JoinRequest {
                map: map.to_string(),
                mode: mode.to_string(),
                room: room.to_string(),
                host: host.to_string(),
                name: name.to_string(),
                ranked,
            },
        ))
    }

    pub fn leave(&self) -> Result<(), NetError> {
        self.send(&Outbound::new("leave", serde_json::json!({})))
    }

    /// Field bots in the match we are in.
    ///
    /// Sent **after the welcome, never with the join**, for the reason the browser
    /// client queues it the same way: `add_bot` needs a room to add them to, and
    /// the room is only ours once the welcome names it. The channel refuses it
    /// from a guest — a bot count only ever travels with `--mode=host`.
    pub fn add_bot(&self, count: u32, skill: &str) -> Result<(), NetError> {
        self.send(&Outbound::new(
            "add_bot",
            serde_json::json!({ "count": count, "skill": skill }),
        ))
    }

    /// Send one developer-console line to the node.
    ///
    /// Sent **immediately**, not queued behind the next `flush`: a console line
    /// is not an input frame, and batching it with movement would make its
    /// latency depend on the frame rate.
    ///
    /// It rides this socket rather than the REST route the browser pane uses
    /// because `channel.py` resolves the room and the player from the connection
    /// itself — a command sent this way lands in the match this client is
    /// actually in, with no room id to get wrong.
    pub fn console_exec(&self, command: &str, req_id: u64) -> Result<(), NetError> {
        self.send(&Outbound::new(
            "console_exec",
            ConsoleExec {
                command,
                req_id,
                context: serde_json::json!({}),
            },
        ))
    }

    /// Queue one input frame. Sent by the next `flush`, not immediately.
    pub fn push_command(&mut self, mut command: Command) -> u64 {
        self.seq += 1;
        command.seq = self.seq;
        self.pending.push(command);
        self.seq
    }

    /// Hand everything queued to the socket thread.
    ///
    /// Rate-limited to `SEND_INTERVAL` and a no-op when there is nothing, so it
    /// is safe — and correct — to call every frame. The limit lives here rather
    /// than at the call site for the reason the browser client gives: a caller
    /// that has to remember to throttle is a caller that will eventually not.
    pub fn flush(&mut self, rtt_ms: Option<f32>) -> Result<(), NetError> {
        if self.pending.is_empty() {
            return Ok(());
        }
        let now = Instant::now();
        if let Some(last) = self.last_send {
            if now.duration_since(last) < SEND_INTERVAL {
                return Ok(());
            }
        }
        self.last_send = Some(now);
        // The server caps a batch at `MAX_COMMANDS_PER_MESSAGE` (64) and drops
        // the rest, so trim here rather than sending frames that will be
        // discarded — and keep the *newest*, since a client that has been stalled
        // legitimately has a backlog and the recent commands are the live ones.
        //
        // Reaching this at all is a bug, and it used to be a *silent* one: every
        // command trimmed here has already been predicted locally under a
        // sequence number the server will never acknowledge, so the prediction
        // walks away from the authoritative position and stays there. Input is
        // produced on its own clock now (`app::INPUT_HZ`, 8.25 commands per
        // flush), so the only way to overflow is a stall longer than two
        // seconds. Say so rather than quietly binning a third of someone's
        // movement — once, because a client that is doing this is doing it every
        // flush and a log line per flush helps nobody.
        let commands = if self.pending.len() > MAX_COMMANDS_PER_MESSAGE {
            let dropped = self.pending.len() - MAX_COMMANDS_PER_MESSAGE;
            if !self.warned_overflow {
                self.warned_overflow = true;
                eprintln!(
                    "hassault: input backlog over {MAX_COMMANDS_PER_MESSAGE} commands;                      dropping the oldest {dropped}. Movement will be corrected                      backwards until it clears."
                );
            }
            self.pending.split_off(dropped)
        } else {
            std::mem::take(&mut self.pending)
        };
        self.pending.clear();
        self.send(&Outbound::new(
            "input",
            InputBatch {
                commands,
                rtt: rtt_ms,
            },
        ))
    }

    /// Send a round-trip probe, at most once per `PING_INTERVAL`.
    ///
    /// The stamp is this process's clock and the server echoes it back
    /// untouched, so the round trip is a subtraction on one clock. Reading the
    /// server's own timestamp instead would be differencing two unrelated
    /// clocks, which is not a duration at all.
    ///
    /// Returns the stamp when one was actually sent, so the caller can pair the
    /// answer with it.
    pub fn ping(&mut self, stamp_ms: f64) -> Result<(), NetError> {
        let now = Instant::now();
        if let Some(last) = self.last_ping {
            if now.duration_since(last) < PING_INTERVAL {
                return Ok(());
            }
        }
        self.last_ping = Some(now);
        self.send(&Outbound::new(
            "ping",
            serde_json::json!({ "t": stamp_ms.round() }),
        ))
    }

    /// Everything that has arrived since the last call. Never blocks.
    pub fn drain(&self) -> Vec<Incoming> {
        let mut out = Vec::new();
        loop {
            match self.rx.try_recv() {
                Ok(item) => out.push(item),
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => {
                    out.push(Incoming::Closed("reader stopped".into()));
                    break;
                }
            }
        }
        out
    }
}

/// The socket's whole life, on one thread.
fn io_loop(mut socket: Socket, inbound: Sender<Incoming>, outbound: Receiver<String>) {
    loop {
        match socket.read() {
            Ok(Message::Text(text)) => {
                if let Some(event) = protocol::classify(&text) {
                    if inbound.send(Incoming::Event(event)).is_err() {
                        return;
                    }
                }
            }
            // Ping/pong and binary are not this protocol's business; tungstenite
            // answers pings itself on the next write.
            Ok(_) => {}
            Err(tungstenite::Error::Io(e)) if is_timeout(&e) => {
                // Nothing to read this cycle. Expected, and the reason the thread
                // gets to do anything else at all.
            }
            Err(e) => {
                let _ = inbound.send(Incoming::Closed(e.to_string()));
                return;
            }
        }

        loop {
            match outbound.try_recv() {
                Ok(text) => {
                    if let Err(e) = socket.send(Message::Text(text)) {
                        let _ = inbound.send(Incoming::Closed(e.to_string()));
                        return;
                    }
                }
                Err(TryRecvError::Empty) => break,
                // The game side is gone: close politely and stop.
                Err(TryRecvError::Disconnected) => {
                    let _ = socket.close(None);
                    return;
                }
            }
        }
    }
}

/// `ERROR_IO_PENDING`. See `is_timeout`.
#[cfg(windows)]
const WINDOWS_IO_PENDING: i32 = 997;

/// Whether an I/O error means "nothing arrived in time" rather than a real fault.
///
/// **Three cases, and the third was found the hard way.** Unix reports a
/// timed-out socket read as `WouldBlock`; the portable Windows answer is
/// `TimedOut`. But on Windows this path actually produces **raw OS error 997,
/// `ERROR_IO_PENDING`**, which Rust maps to no named `ErrorKind` at all — so a
/// classifier that checks only the two obvious kinds treats an ordinary timeout
/// as a dead socket. The symptom is precise and misleading: the client connects,
/// joins a match, renders one frame, and then prints
/// *"connection closed: Overlapped I/O operation is in progress"* — which reads
/// as a network fault, on a loopback connection to a server on the same machine
/// that is perfectly healthy.
///
/// `Interrupted` is here for the usual reason: a signal is not an error.
fn is_timeout(e: &std::io::Error) -> bool {
    if matches!(
        e.kind(),
        std::io::ErrorKind::WouldBlock
            | std::io::ErrorKind::TimedOut
            | std::io::ErrorKind::Interrupted
    ) {
        return true;
    }
    #[cfg(windows)]
    if e.raw_os_error() == Some(WINDOWS_IO_PENDING) {
        return true;
    }
    false
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn connecting_to_nothing_fails_with_a_reason() {
        // Port 1 is reserved and nothing listens there. The point is that this
        // returns an error rather than panicking or hanging: "the node is not
        // running" is the single most common thing that will go wrong, and it
        // has to be sayable.
        match MatchSocket::connect("ws://127.0.0.1:1/ws") {
            Err(NetError::Connect(_)) => {}
            Err(other) => panic!("expected a connect error, got {other}"),
            Ok(_) => panic!("nothing should be listening on port 1"),
        }
    }

    #[test]
    fn every_flavour_of_timeout_is_read_as_nothing_to_read() {
        // Unix says WouldBlock, the portable Windows answer is TimedOut, and a
        // signal says Interrupted. Accepting one and not the others makes the
        // client disconnect immediately on some platform, blaming the network for
        // a match arm.
        assert!(is_timeout(&std::io::Error::from(
            std::io::ErrorKind::WouldBlock
        )));
        assert!(is_timeout(&std::io::Error::from(
            std::io::ErrorKind::TimedOut
        )));
        assert!(is_timeout(&std::io::Error::from(
            std::io::ErrorKind::Interrupted
        )));
        assert!(!is_timeout(&std::io::Error::from(
            std::io::ErrorKind::ConnectionReset
        )));
    }

    #[cfg(windows)]
    #[test]
    fn windows_reports_a_timed_out_read_as_error_997() {
        // Observed, not assumed: this is what a `SO_RCVTIMEO` expiry actually
        // produces here, and it carries no named `ErrorKind`, so nothing but the
        // raw code identifies it. Without this arm the client joins a match and
        // then immediately reports the connection closed.
        let e = std::io::Error::from_raw_os_error(WINDOWS_IO_PENDING);
        assert!(
            !matches!(
                e.kind(),
                std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
            ),
            "if Rust ever names this kind, the raw-code arm can go"
        );
        assert!(is_timeout(&e));
    }
}

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
use std::time::Duration;

use tungstenite::stream::MaybeTlsStream;
use tungstenite::{Message, WebSocket};

use crate::protocol::{self, Command, Event, InputBatch, JoinRequest, Outbound};

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

pub struct MatchSocket {
    outbound: Sender<String>,
    rx: Receiver<Incoming>,
    seq: u64,
    /// Commands accumulated since the last flush.
    pending: Vec<Command>,
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

    /// Ask to join. `room`/`host` empty means "a match on this node, on this map".
    pub fn join(&self, map: &str, room: &str, host: &str, name: &str) -> Result<(), NetError> {
        self.send(&Outbound::new(
            "join",
            JoinRequest {
                map: map.to_string(),
                room: room.to_string(),
                host: host.to_string(),
                name: name.to_string(),
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

    /// Queue one input frame. Sent by the next `flush`, not immediately.
    pub fn push_command(&mut self, mut command: Command) -> u64 {
        self.seq += 1;
        command.seq = self.seq;
        self.pending.push(command);
        self.seq
    }

    /// Hand everything queued to the socket thread. A no-op when there is
    /// nothing, so it is safe to call every frame.
    pub fn flush(&mut self, rtt_ms: Option<f32>) -> Result<(), NetError> {
        if self.pending.is_empty() {
            return Ok(());
        }
        // The server caps a batch at `MAX_COMMANDS_PER_MESSAGE` (64) and drops
        // the rest, so trim here rather than sending frames that will be
        // discarded — and keep the *newest*, since a client that has been stalled
        // legitimately has a backlog and the recent commands are the live ones.
        let commands = if self.pending.len() > 64 {
            self.pending.split_off(self.pending.len() - 64)
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

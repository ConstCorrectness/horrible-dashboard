//! In-game text chat: everyone ([Y] by default) and team ([U]), both rebindable
//! through the shared key map.
//!
//! Text is edited in **grapheme clusters**, not `char`s: 👨‍👩‍👧‍👦 is seven
//! `char`s joined by zero-width joiners, and a backspace that removed one `char`
//! would leave 👨‍👩‍👧 plus a dangling joiner on screen. The limits are the server's
//! (`backend/modules/hassault/chat.py`): 200 clusters and 1024 UTF-8 bytes,
//! applied at a cluster boundary, so the prompt never accepts what the server
//! would cut.
//!
//! Lines are the **server's copy**, deduplicated by id. The client used to echo
//! its own message locally *and* receive the server's echo, so every line it sent
//! appeared twice.

use std::collections::VecDeque;

use unicode_segmentation::UnicodeSegmentation;

/// How long a chat message stays visible in seconds when chat is closed.
pub const CHAT_TTL: f32 = 8.0;

/// Maximum chat history entries preserved in memory.
pub const MAX_CHAT_MESSAGES: usize = 60;

/// The server's limits — `MAX_GRAPHEMES` and `MAX_BYTES` in `chat.py`.
pub const MAX_GRAPHEMES: usize = 200;
pub const MAX_BYTES: usize = 1024;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChatChannel {
    All,
    Team,
}

#[derive(Debug, Clone)]
pub struct ChatMessage {
    /// The server's id; empty from a server that predates it.
    pub id: String,
    pub sender_id: String,
    pub sender_name: String,
    pub team: i32,
    pub is_team: bool,
    pub text: String,
    pub age: f32,
}

#[derive(Debug, Clone)]
pub struct ChatState {
    pub open: bool,
    pub channel: ChatChannel,
    pub input: String,
    /// Byte offset of the caret in `input`, always on a cluster boundary.
    pub cursor: usize,
    /// Text an input method is still composing (CJK candidates). Drawn at the
    /// caret, never part of `input` until committed.
    pub preedit: String,
    pub messages: VecDeque<ChatMessage>,
    /// Bumped on every change a renderer would draw differently, so the text
    /// layer re-rasterizes only when something actually changed.
    pub revision: u64,
}

impl Default for ChatState {
    fn default() -> Self {
        Self::new()
    }
}

impl ChatState {
    pub fn new() -> Self {
        Self {
            open: false,
            channel: ChatChannel::All,
            input: String::new(),
            cursor: 0,
            preedit: String::new(),
            messages: VecDeque::with_capacity(MAX_CHAT_MESSAGES),
            revision: 0,
        }
    }

    fn touch(&mut self) {
        self.revision = self.revision.wrapping_add(1);
    }

    pub fn open_prompt(&mut self, channel: ChatChannel) {
        self.open = true;
        self.channel = channel;
        self.input.clear();
        self.preedit.clear();
        self.cursor = 0;
        self.touch();
    }

    pub fn close_prompt(&mut self) {
        self.open = false;
        self.input.clear();
        self.preedit.clear();
        self.cursor = 0;
        self.touch();
    }

    pub fn toggle_channel(&mut self) {
        self.channel = match self.channel {
            ChatChannel::All => ChatChannel::Team,
            ChatChannel::Team => ChatChannel::All,
        };
        self.touch();
    }

    pub fn step(&mut self, dt: f32) {
        for msg in &mut self.messages {
            msg.age += dt;
        }
    }

    /// Add a line the server sent. A repeated id is dropped — a frame can be
    /// delivered twice across a reconnect, and it is one message.
    pub fn receive(
        &mut self,
        id: impl Into<String>,
        sender_id: impl Into<String>,
        sender_name: impl Into<String>,
        team: i32,
        is_team: bool,
        text: impl Into<String>,
    ) {
        let id = id.into();
        let text = text.into();
        if text.trim().is_empty() {
            return;
        }
        if !id.is_empty() && self.messages.iter().any(|m| m.id == id) {
            return;
        }
        self.messages.push_front(ChatMessage {
            id,
            sender_id: sender_id.into(),
            sender_name: sender_name.into(),
            team,
            is_team,
            text,
            age: 0.0,
        });
        while self.messages.len() > MAX_CHAT_MESSAGES {
            self.messages.pop_back();
        }
        self.touch();
    }

    /// Insert text at the caret, as much of it as fits — cut between clusters.
    pub fn type_text(&mut self, text: &str) {
        let mut clusters = self.input.graphemes(true).count();
        let mut bytes = self.input.len();
        let mut changed = false;
        for cluster in text.graphemes(true) {
            if cluster.chars().any(|c| c.is_control()) {
                continue;
            }
            if clusters >= MAX_GRAPHEMES || bytes + cluster.len() > MAX_BYTES {
                break;
            }
            self.input.insert_str(self.cursor, cluster);
            self.cursor += cluster.len();
            clusters += 1;
            bytes += cluster.len();
            changed = true;
        }
        // Inserting can merge with a neighbour into one cluster (a skin tone
        // typed after a hand). Keep the caret on a boundary regardless.
        self.cursor = self.snap(self.cursor);
        if changed {
            self.touch();
        }
    }

    pub fn type_char(&mut self, c: char) {
        let mut buf = [0u8; 4];
        self.type_text(c.encode_utf8(&mut buf));
    }

    /// Delete the whole cluster before the caret.
    pub fn backspace(&mut self) {
        if let Some(start) = self.prev_boundary(self.cursor) {
            self.input.replace_range(start..self.cursor, "");
            self.cursor = start;
            self.touch();
        }
    }

    /// Delete the whole cluster after the caret.
    pub fn delete(&mut self) {
        if let Some(end) = self.next_boundary(self.cursor) {
            self.input.replace_range(self.cursor..end, "");
            self.touch();
        }
    }

    pub fn move_cursor_left(&mut self) {
        if let Some(start) = self.prev_boundary(self.cursor) {
            self.cursor = start;
            self.touch();
        }
    }

    pub fn move_cursor_right(&mut self) {
        if let Some(end) = self.next_boundary(self.cursor) {
            self.cursor = end;
            self.touch();
        }
    }

    pub fn cursor_home(&mut self) {
        self.cursor = 0;
        self.touch();
    }

    pub fn cursor_end(&mut self) {
        self.cursor = self.input.len();
        self.touch();
    }

    /// Submits active input. Returns `Some((text, is_team))` if text was non-empty.
    /// Leaves the prompt's open state to the caller, which also owns the IME.
    pub fn submit(&mut self) -> Option<(String, bool)> {
        let raw = self.input.trim().to_string();
        let is_team = self.channel == ChatChannel::Team;
        self.input.clear();
        self.cursor = 0;
        self.touch();
        if raw.is_empty() {
            None
        } else {
            Some((raw, is_team))
        }
    }

    fn prev_boundary(&self, at: usize) -> Option<usize> {
        self.input[..at]
            .grapheme_indices(true)
            .next_back()
            .map(|(i, _)| i)
    }

    fn next_boundary(&self, at: usize) -> Option<usize> {
        self.input[at..]
            .graphemes(true)
            .next()
            .map(|g| at + g.len())
    }

    /// The first cluster boundary at or after `at`.
    fn snap(&self, at: usize) -> usize {
        for (i, g) in self.input.grapheme_indices(true) {
            if i >= at {
                return i;
            }
            if i + g.len() >= at {
                return i + g.len();
            }
        }
        self.input.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const FAMILY: &str = "\u{1F468}\u{200D}\u{1F469}\u{200D}\u{1F467}\u{200D}\u{1F466}";
    const THUMB: &str = "\u{1F44D}\u{1F3FD}";

    #[test]
    fn chat_prompt_open_and_type() {
        let mut chat = ChatState::new();
        assert!(!chat.open);
        chat.open_prompt(ChatChannel::Team);
        assert!(chat.open);
        assert_eq!(chat.channel, ChatChannel::Team);

        chat.type_text("Rush B");
        assert_eq!(chat.input, "Rush B");
        assert_eq!(chat.cursor, 6);

        chat.backspace();
        assert_eq!(chat.input, "Rush ");
        chat.type_char('A');
        assert_eq!(chat.input, "Rush A");

        let submitted = chat.submit();
        assert_eq!(submitted, Some(("Rush A".to_string(), true)));
        assert!(chat.input.is_empty());
    }

    #[test]
    fn backspace_removes_a_whole_emoji() {
        let mut chat = ChatState::new();
        chat.open_prompt(ChatChannel::All);
        chat.type_text(&format!("gg {FAMILY}{THUMB}"));
        chat.backspace();
        assert_eq!(chat.input, format!("gg {FAMILY}"));
        chat.backspace();
        assert_eq!(chat.input, "gg ");
    }

    #[test]
    fn the_caret_steps_over_clusters() {
        let mut chat = ChatState::new();
        chat.type_text(&format!("a{FAMILY}b"));
        chat.cursor_home();
        chat.move_cursor_right();
        chat.move_cursor_right();
        assert_eq!(chat.cursor, 1 + FAMILY.len());
        chat.delete();
        assert_eq!(chat.input, format!("a{FAMILY}"));
    }

    #[test]
    fn the_limit_never_cuts_an_emoji() {
        let mut chat = ChatState::new();
        chat.type_text(&FAMILY.repeat(100));
        // 25 bytes each: the byte ceiling decides, and each one is whole.
        assert_eq!(chat.input, FAMILY.repeat(MAX_BYTES / FAMILY.len()));
        chat.input.clear();
        chat.cursor = 0;
        chat.type_text(&"é".repeat(500));
        assert_eq!(chat.input.graphemes(true).count(), MAX_GRAPHEMES);
    }

    #[test]
    fn a_line_delivered_twice_is_one_line() {
        let mut chat = ChatState::new();
        chat.receive("m1", "p1", "Alice", 0, false, "Hello 🌍");
        chat.receive("m1", "p1", "Alice", 0, false, "Hello 🌍");
        assert_eq!(chat.messages.len(), 1);
        assert_eq!(chat.messages[0].text, "Hello 🌍");
    }
}

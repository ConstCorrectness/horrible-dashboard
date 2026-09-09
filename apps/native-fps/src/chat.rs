//! In-game text chat system (All Chat [Y] and Team Chat [U]).
//!
//! Provides text input capture, history buffer with auto-fade, and channel segregation.

use std::collections::VecDeque;

/// How long a chat message stays visible in seconds when chat is closed.
pub const CHAT_TTL: f32 = 8.0;

/// Maximum chat history entries preserved in memory.
pub const MAX_CHAT_MESSAGES: usize = 60;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChatChannel {
    All,
    Team,
}

#[derive(Debug, Clone)]
pub struct ChatMessage {
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
    pub cursor: usize,
    pub messages: VecDeque<ChatMessage>,
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
            messages: VecDeque::with_capacity(MAX_CHAT_MESSAGES),
        }
    }

    pub fn open_prompt(&mut self, channel: ChatChannel) {
        self.open = true;
        self.channel = channel;
        self.input.clear();
        self.cursor = 0;
    }

    pub fn close_prompt(&mut self) {
        self.open = false;
        self.input.clear();
        self.cursor = 0;
    }

    pub fn step(&mut self, dt: f32) {
        for msg in &mut self.messages {
            msg.age += dt;
        }
    }

    pub fn receive(
        &mut self,
        sender_id: impl Into<String>,
        sender_name: impl Into<String>,
        team: i32,
        is_team: bool,
        text: impl Into<String>,
    ) {
        let text = text.into();
        if text.trim().is_empty() {
            return;
        }
        self.messages.push_front(ChatMessage {
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
    }

    pub fn type_char(&mut self, c: char) {
        if c.is_control() {
            return;
        }
        if self.input.len() >= 140 {
            return;
        }
        self.input.insert(self.cursor, c);
        self.cursor += c.len_utf8();
    }

    pub fn type_text(&mut self, text: &str) {
        for c in text.chars() {
            self.type_char(c);
        }
    }

    pub fn backspace(&mut self) {
        if self.cursor > 0 {
            let prev = self.input[..self.cursor]
                .char_indices()
                .last()
                .map(|(idx, _)| idx)
                .unwrap_or(0);
            self.input.remove(prev);
            self.cursor = prev;
        }
    }

    pub fn delete(&mut self) {
        if self.cursor < self.input.len() {
            self.input.remove(self.cursor);
        }
    }

    pub fn move_cursor_left(&mut self) {
        if self.cursor > 0 {
            if let Some((idx, _)) = self.input[..self.cursor].char_indices().last() {
                self.cursor = idx;
            }
        }
    }

    pub fn move_cursor_right(&mut self) {
        if self.cursor < self.input.len() {
            if let Some(ch) = self.input[self.cursor..].chars().next() {
                self.cursor += ch.len_utf8();
            }
        }
    }

    pub fn cursor_home(&mut self) {
        self.cursor = 0;
    }

    pub fn cursor_end(&mut self) {
        self.cursor = self.input.len();
    }

    /// Submits active input. Returns `Some((text, is_team))` if text was non-empty.
    pub fn submit(&mut self) -> Option<(String, bool)> {
        let raw = self.input.trim().to_string();
        let is_team = self.channel == ChatChannel::Team;
        self.close_prompt();
        if raw.is_empty() {
            None
        } else {
            Some((raw, is_team))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
        assert!(!chat.open);
        assert!(chat.input.is_empty());
    }

    #[test]
    fn chat_receive_and_fifo() {
        let mut chat = ChatState::new();
        chat.receive("p1", "Alice", 0, false, "Hello world");
        assert_eq!(chat.messages.len(), 1);
        let msg = &chat.messages[0];
        assert_eq!(msg.sender_name, "Alice");
        assert_eq!(msg.text, "Hello world");
        assert!(!msg.is_team);
    }
}

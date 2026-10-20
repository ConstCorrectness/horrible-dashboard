//! The buy menu's shape: groups of rows, and what a number key does.
//!
//! A port of the browser's `buy-menu.ts`, pinned to the server's catalogue by
//! `economy-vectors.json`. The catalogue outgrew one row of keys, so the menu
//! is CS's: a key opens a group, a second key buys a row in it, and `0` goes
//! back. Only the rows the player's side may buy are listed, but each keeps its
//! catalogue index, because that index is `Command::buy` and the server's.

use crate::cosmetics::side_of;
use crate::protocol::BuyItem;

/// The groups, in menu order, with the labels both clients show.
pub const GROUPS: [(&str, &str); 4] = [
    ("secondary", "PISTOLS"),
    ("primary", "PRIMARY"),
    ("gear", "GEAR"),
    ("grenade", "GRENADES"),
];

#[derive(Debug, Clone, PartialEq)]
pub struct BuyGroup {
    pub category: &'static str,
    pub label: &'static str,
    /// Catalogue indexes: what goes on the wire.
    pub rows: Vec<usize>,
}

/// A row's group: the served `category`, or (from an older server, which sent
/// none) what its `kind` implies.
pub fn category_of(item: &BuyItem) -> &'static str {
    if let Some((category, _)) = GROUPS.iter().find(|(c, _)| *c == item.category) {
        return category;
    }
    match item.kind.as_str() {
        "weapon" => "primary",
        "nade" => "grenade",
        _ => "gear",
    }
}

/// The groups `team` sees, in menu order, empty ones left out.
pub fn buy_groups(catalog: &[BuyItem], team: i32) -> Vec<BuyGroup> {
    let side = side_of(team);
    GROUPS
        .iter()
        .map(|(category, label)| BuyGroup {
            category,
            label,
            rows: catalog
                .iter()
                .enumerate()
                .filter(|(_, item)| matches!(item.side.as_str(), "" | "any") || item.side == side)
                .filter(|(_, item)| category_of(item) == *category)
                .map(|(index, _)| index)
                .collect(),
        })
        .filter(|g| !g.rows.is_empty())
        .collect()
}

/// One number key in the held menu: `digit` 1..9, or 0 for back. Returns the
/// group now open (`None` is the top level) and the catalogue index to buy, if
/// any. A buy returns to the top, as CS does.
pub fn press(
    groups: &[BuyGroup],
    open: Option<usize>,
    digit: usize,
) -> (Option<usize>, Option<usize>) {
    if digit == 0 {
        return (None, None);
    }
    match open {
        None => ((digit - 1 < groups.len()).then_some(digit - 1), None),
        Some(group) => match groups.get(group).and_then(|g| g.rows.get(digit - 1)) {
            Some(&index) => (None, Some(index)),
            None => (Some(group), None),
        },
    }
}

/// Why a buy now would be refused, in a few words, or empty when it would land.
pub fn notice(can_buy: bool, buy_in: f32, in_zone: bool) -> &'static str {
    if can_buy {
        ""
    } else if buy_in <= 0.0 {
        "BUY TIME IS OVER"
    } else if !in_zone {
        "OUTSIDE THE BUY ZONE"
    } else {
        "THE WINDOW IS CLOSED"
    }
}

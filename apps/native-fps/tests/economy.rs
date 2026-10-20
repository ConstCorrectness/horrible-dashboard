//! The buy menu and the trade commands, against `economy-vectors.json`, which
//! `scripts/gen_hassault_economy_vectors.py` writes from `modes/defuse.py`.
//!
//! The same claims `buy-menu.test.ts` makes of the browser: each side sees the
//! rows it may buy under the groups the server's table implies, and a key
//! sequence buys the catalogue index the server will read. Plus this client's
//! own half of the wire: a sale and a drop ride the command, and stay off it
//! when there is nothing to say.

use std::path::PathBuf;

use hassault_native::buy_menu::{buy_groups, category_of, notice, press};
use hassault_native::protocol::{BuyItem, Command, ModeSelf};
use serde_json::Value;

fn vectors() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../packages/core/src/modules/hassault/__tests__/economy-vectors.json");
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("economy vectors missing at {} ({e})", path.display()));
    serde_json::from_str(&text).expect("economy vectors are not valid JSON")
}

fn catalog(v: &Value) -> Vec<BuyItem> {
    serde_json::from_value(v["catalog"].clone()).expect("the catalogue reads as BuyItem")
}

#[test]
fn each_side_sees_the_groups_the_server_implies() {
    let v = vectors();
    let items = catalog(&v);
    for (side, team) in [("t", 0), ("ct", 1)] {
        let groups = buy_groups(&items, team);
        let want = v["menus"][side].as_array().unwrap();
        assert_eq!(groups.len(), want.len(), "{side}");
        for (got, want) in groups.iter().zip(want) {
            assert_eq!(got.category, want["category"].as_str().unwrap());
            assert_eq!(got.label, want["label"].as_str().unwrap());
            let rows: Vec<usize> = want["rows"]
                .as_array()
                .unwrap()
                .iter()
                .map(|r| r.as_u64().unwrap() as usize)
                .collect();
            assert_eq!(got.rows, rows, "{side} {}", got.category);
            assert!(got.rows.len() <= 9, "a group must fit the number row");
        }
    }
}

#[test]
fn a_group_key_then_a_row_key_buys_and_zero_goes_back() {
    let items = catalog(&vectors());
    let groups = buy_groups(&items, 1);
    let primary = groups.iter().position(|g| g.category == "primary").unwrap();
    assert_eq!(press(&groups, None, primary + 1), (Some(primary), None));
    assert_eq!(
        press(&groups, Some(primary), 2),
        (None, Some(groups[primary].rows[1]))
    );
    assert_eq!(press(&groups, Some(primary), 0), (None, None));
    assert_eq!(press(&groups, Some(primary), 9).1, None);
    assert_eq!(press(&groups, None, 9).0, None);
}

#[test]
fn an_older_servers_rows_are_grouped_by_kind() {
    let item = |kind: &str| BuyItem {
        kind: kind.into(),
        ..Default::default()
    };
    assert_eq!(category_of(&item("weapon")), "primary");
    assert_eq!(category_of(&item("nade")), "grenade");
    assert_eq!(category_of(&item("armour")), "gear");
}

#[test]
fn a_sale_and_a_drop_ride_the_command_only_when_asked() {
    let quiet = serde_json::to_value(Command::new(1)).unwrap();
    assert!(quiet.get("sell").is_none() && quiet.get("drop").is_none());
    assert!(quiet.get("buy").is_none());
    let mut cmd = Command::new(2);
    cmd.sell = 4;
    cmd.drop = true;
    let sent = serde_json::to_value(cmd).unwrap();
    assert_eq!(sent["sell"], 4);
    assert_eq!(sent["drop"], true);
}

#[test]
fn the_envelope_says_why_a_buy_would_be_refused() {
    let mine: ModeSelf = serde_json::from_str(
        r#"{"money": 800, "canBuy": false, "buyIn": 12.5, "inBuyZone": false, "sellable": [3]}"#,
    )
    .unwrap();
    assert_eq!(mine.sellable, vec![3]);
    assert_eq!(
        notice(mine.can_buy, mine.buy_in, mine.in_buy_zone),
        "OUTSIDE THE BUY ZONE"
    );
    // An older server sends no zone: everywhere was one.
    let old: ModeSelf = serde_json::from_str(r#"{"money": 800, "canBuy": false}"#).unwrap();
    assert!(old.in_buy_zone);
    assert_eq!(
        notice(old.can_buy, old.buy_in, old.in_buy_zone),
        "BUY TIME IS OVER"
    );
    assert_eq!(notice(true, 3.0, true), "");
}

#[test]
fn the_store_page_reads_the_wallet_and_offers_each_case() {
    use hassault_native::api::Wallet;
    use hassault_native::controls::Controls;
    use hassault_native::menu::{Action, Menu, Page};
    use hassault_native::settings::Settings;

    let wallet: Wallet = serde_json::from_str(
        r#"{"balance": 340, "keyPrice": 250, "keys": 1, "history": [],
            "cases": [{"instanceId": "c1", "caseId": "field_case", "name": "Field Case"}]}"#,
    )
    .unwrap();
    let mut menu = Menu::default();
    let (settings, keys) = (Settings::default(), Controls::default());
    assert!(!menu.rows(&settings, &keys, false).iter().any(|r| r.label == "STORE"));
    menu.store_available = true;
    assert!(menu.rows(&settings, &keys, false).iter().any(|r| r.label == "STORE"));

    menu.page = Page::Store;
    menu.store.wallet = Some(wallet);
    let rows = menu.rows(&settings, &keys, false);
    let find = |label: &str| rows.iter().find(|r| r.label == label).unwrap();
    assert_eq!(find("CREDITS").value, "340");
    assert_eq!(find("BUY KEY").action, Action::BuyKey);
    assert_eq!(find("OPEN FIELD CASE").action, Action::OpenCase(0));
    assert_eq!(find("OPEN FIELD CASE").value, "1 KEY");
}

#[test]
fn the_market_page_lists_the_cheapest_listings_to_buy() {
    use hassault_native::api::Listing;
    use hassault_native::controls::Controls;
    use hassault_native::menu::{Action, Menu, Page};
    use hassault_native::settings::Settings;

    let listings: Vec<Listing> = serde_json::from_str(
        r#"[{"listingId": "l1", "price": 120, "kind": "skin",
             "item": {"instanceId": "i1", "definition": {"name": "Desert Fade"}}},
            {"listingId": "l2", "price": 300, "kind": "case",
             "item": {"instanceId": "i2", "defId": "field_case"}}]"#,
    )
    .unwrap();
    let mut menu = Menu::default();
    let (settings, keys) = (Settings::default(), Controls::default());
    menu.store_available = true;
    assert!(menu.rows(&settings, &keys, false).iter().any(|r| r.label == "MARKET"));
    menu.page = Page::Market;
    menu.store.listings = Some(listings);
    let rows = menu.rows(&settings, &keys, false);
    let skin = rows.iter().find(|r| r.label == "BUY DESERT FADE").unwrap();
    assert_eq!((skin.value.as_str(), skin.action), ("120", Action::BuyListing(0)));
    assert!(rows.iter().any(|r| r.label == "BUY FIELD CASE" && r.value == "300"));
}

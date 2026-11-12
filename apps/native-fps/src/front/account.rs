//! The account screens: signing in, the inventory, the store.
//!
//! A guest plays, and owns nothing: skins, credits, cases and a rating belong
//! to an account. These two tabs are where the account is, and for a guest
//! they are where one is signed in to.
//!
//! **No password is ever typed here.** Signing in goes through the provider's
//! own page in the player's browser — the game server's redirect flow, or its
//! device flow (a short code typed at the provider) where a server has no
//! redirect client. This screen starts it, says where to finish it, and waits.
//! What comes back is the server's token, which is all this client keeps.
//!
//! A child of `front` so it can lay out with the same private pieces — the
//! margins, the cards, the one big button — without those becoming public.

use super::*;

/// The sign-in providers, in button order: the server's id, and the label.
pub const PROVIDERS: [(&str, &str); 2] = [("github", "GITHUB"), ("google", "GOOGLE")];

/// Who is signed in.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct AccountView {
    pub name: String,
    /// The unique username, once one has been chosen.
    pub handle: Option<String>,
}

/// Where a sign-in has got to.
#[derive(Debug, Clone, Default, PartialEq)]
pub enum SignIn {
    #[default]
    Idle,
    /// Asked the server to begin; nothing to show yet.
    Starting(usize),
    /// The rest happens in the browser. `code` is set for the device flow,
    /// where the player types it at `url`.
    Waiting {
        provider: usize,
        url: String,
        code: Option<String>,
    },
    Failed(String),
}

/// One owned skin.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ItemView {
    pub instance_id: String,
    pub name: String,
    pub weapon_id: String,
    pub wear: f32,
    pub equipped: bool,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct CaseView {
    pub instance_id: String,
    pub name: String,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct WalletView {
    pub balance: i64,
    pub keys: i64,
    pub key_price: i64,
    pub cases: Vec<CaseView>,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct ListingView {
    pub id: String,
    pub name: String,
    pub kind: String,
    pub price: i64,
}

/// Wear as the five grades a skin's float falls into.
pub fn wear_label(wear: f32) -> &'static str {
    match wear {
        w if w < 0.07 => "FACTORY NEW",
        w if w < 0.15 => "MINIMAL WEAR",
        w if w < 0.38 => "FIELD TESTED",
        w if w < 0.45 => "WELL WORN",
        _ => "BATTLE SCARRED",
    }
}

const ITEM_W: f32 = 204.0;
const ITEM_H: f32 = 70.0;
const ROW_H: f32 = 38.0;
/// Cases listed in the wallet column; more are counted, not drawn.
const CASES_SHOWN: usize = 5;

impl Front {
    /// Forget everything an account brought with it.
    pub fn signed_out(&mut self) {
        self.account = None;
        self.items.clear();
        self.wallet = None;
        self.signin = SignIn::Idle;
    }

    pub(super) fn account_hits(
        &self,
        width: f32,
        height: f32,
        k: f32,
        top: f32,
        out: &mut Vec<(Target, Hit)>,
    ) {
        let left = MARGIN * k;
        let right = width - MARGIN * k;
        let wide = |y: f32, h: f32| Hit {
            x: left,
            y,
            w: COLUMN * k,
            h,
        };
        if self.account.is_none() {
            match self.signin {
                SignIn::Idle | SignIn::Failed(_) => {
                    for i in 0..PROVIDERS.len() {
                        out.push((
                            Target::Provider(i),
                            wide(top + (150.0 + 52.0 * i as f32) * k, 44.0 * k),
                        ));
                    }
                }
                SignIn::Starting(_) | SignIn::Waiting { .. } => {
                    out.push((Target::SignInCancel, wide(top + 236.0 * k, 40.0 * k)));
                }
            }
            return;
        }
        let area_x = left + (COLUMN + 32.0) * k;
        let area_y = top + 22.0 * k;
        let floor = height - (MARGIN + GO_H + 20.0) * k;
        if self.tab == Tab::Inventory {
            out.push((Target::AccountRefresh, wide(top + 112.0 * k, 36.0 * k)));
            out.push((Target::SignOut, wide(top + 156.0 * k, 36.0 * k)));
            let (w, h, gap) = (ITEM_W * k, ITEM_H * k, 10.0 * k);
            let columns = (((right - area_x) + gap) / (w + gap)).floor().max(1.0) as usize;
            for i in 0..self.items.len() {
                let (col, row) = (i % columns, i / columns);
                let y = area_y + row as f32 * (h + gap);
                if y + h > height - MARGIN * k {
                    break;
                }
                out.push((
                    Target::Item(i),
                    Hit {
                        x: area_x + col as f32 * (w + gap),
                        y,
                        w,
                        h,
                    },
                ));
            }
            return;
        }
        // The store: the wallet down the left, the market on the right.
        out.push((Target::BuyKey, wide(top + 130.0 * k, 36.0 * k)));
        let cases = self.wallet.as_ref().map_or(0, |w| w.cases.len());
        for i in 0..cases.min(CASES_SHOWN) {
            out.push((
                Target::Case(i),
                wide(top + (214.0 + 34.0 * i as f32) * k, 30.0 * k),
            ));
        }
        for i in 0..self.listings.len() {
            let y = area_y + 30.0 * k + i as f32 * ROW_H * k;
            if y + ROW_H * k > floor {
                break;
            }
            out.push((
                Target::Listing(i),
                Hit {
                    x: area_x,
                    y,
                    w: right - area_x,
                    h: (ROW_H - 4.0) * k,
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

    pub(super) fn account_activate(&mut self, target: Target) -> Option<Intent> {
        match target {
            Target::Provider(i) => {
                if self.providers.get(i).copied().unwrap_or(false) {
                    self.signin = SignIn::Starting(i);
                    return Some(Intent::SignIn(i));
                }
                self.notice = Some((
                    format!("THIS SERVER HAS NO {} SIGN-IN SET UP", PROVIDERS[i].1),
                    true,
                ));
            }
            Target::SignInCancel => {
                self.signin = SignIn::Idle;
                return Some(Intent::SignInCancel);
            }
            Target::SignOut => return Some(Intent::SignOut),
            Target::AccountRefresh => return Some(Intent::AccountRefresh),
            Target::Item(i) => {
                let item = self.items.get(i)?;
                return Some(if item.equipped {
                    Intent::Unequip(item.weapon_id.clone())
                } else {
                    Intent::Equip(item.instance_id.clone())
                });
            }
            Target::BuyKey => return Some(Intent::BuyKey),
            Target::Case(i) => {
                let wallet = self.wallet.as_ref()?;
                if wallet.keys < 1 {
                    self.notice = Some(("A CASE NEEDS A KEY TO OPEN - BUY ONE FIRST".into(), true));
                    return None;
                }
                return wallet
                    .cases
                    .get(i)
                    .map(|c| Intent::OpenCase(c.instance_id.clone()));
            }
            Target::Listing(i) => self.listing = i,
            _ => {}
        }
        None
    }

    /// The store's button: buy the selected listing, if it can be afforded.
    pub(super) fn account_go(&mut self) -> Option<Intent> {
        self.notice = None;
        let listing = self.listings.get(self.listing)?;
        let balance = self.wallet.as_ref().map_or(0, |w| w.balance);
        if listing.price > balance {
            self.notice = Some((
                format!(
                    "{} COSTS {} CREDITS - YOU HAVE {}",
                    listing.name.to_uppercase(),
                    listing.price,
                    balance
                ),
                true,
            ));
            return None;
        }
        Some(Intent::BuyListing(listing.id.clone()))
    }

    pub(super) fn paint_account(
        &self,
        p: &mut Painter,
        hits: &[(Target, Hit)],
        width: f32,
        k: f32,
        top: f32,
    ) {
        let left = MARGIN * k;
        let right = width - MARGIN * k;
        let area_x = left + (COLUMN + 32.0) * k;
        let Some(account) = &self.account else {
            return self.paint_signin(p, hits, k, top);
        };

        if self.tab == Tab::Inventory {
            heading(p, left, top, k, "ACCOUNT");
            p.name(left, top + 30.0 * k, 2.2 * k, theme::AMBER, &account.name);
            let handle = match &account.handle {
                Some(handle) => format!("USERNAME  {}", handle.to_uppercase()),
                None => "NO USERNAME CHOSEN YET".to_string(),
            };
            p.num(left, top + 62.0 * k, 1.1 * k, theme::TEXT_DIM, &handle);
            p.num(
                left,
                top + 84.0 * k,
                1.0 * k,
                theme::FAINT,
                "CLICK A SKIN TO EQUIP IT OR TAKE IT OFF",
            );
            heading(
                p,
                area_x,
                top,
                k,
                &format!("INVENTORY  /  {} SKINS", self.items.len()),
            );
            if self.items.is_empty() {
                p.num(
                    area_x,
                    top + 40.0 * k,
                    1.2 * k,
                    theme::FAINT,
                    "NOTHING YET - SKINS COME FROM LEVEL DROPS, CASES AND THE MARKET",
                );
            }
        } else {
            heading(p, left, top, k, "WALLET");
            let (balance, keys, price) = self
                .wallet
                .as_ref()
                .map_or((0, 0, 0), |w| (w.balance, w.keys, w.key_price));
            p.num(
                left,
                top + 30.0 * k,
                2.6 * k,
                theme::AMBER,
                &balance.to_string(),
            );
            p.num(left, top + 66.0 * k, 1.05 * k, theme::FAINT, "CREDITS");
            p.num_right(
                left + COLUMN * k,
                top + 34.0 * k,
                2.0 * k,
                theme::TEXT,
                &keys.to_string(),
            );
            p.num_right(
                left + COLUMN * k,
                top + 66.0 * k,
                1.05 * k,
                theme::FAINT,
                if keys == 1 { "KEY" } else { "KEYS" },
            );
            let _ = price;
            let cases = self.wallet.as_ref().map_or(0, |w| w.cases.len());
            p.text(
                left,
                top + 196.0 * k,
                1.05 * k,
                theme::FAINT,
                &match cases {
                    0 => "NO CASES - THEY DROP AS YOU LEVEL".to_string(),
                    n if n > CASES_SHOWN => format!("CASES  /  {n}, CLICK ONE TO OPEN IT"),
                    _ => "CASES  /  CLICK ONE TO OPEN IT".to_string(),
                },
            );
            heading(p, area_x, top, k, "MARKET");
            if self.listings.is_empty() {
                p.num(
                    area_x,
                    top + 62.0 * k,
                    1.2 * k,
                    theme::FAINT,
                    "NOTHING IS LISTED FOR SALE RIGHT NOW",
                );
            } else {
                let y = top + 28.0 * k;
                p.num(area_x + 14.0 * k, y, 1.0 * k, theme::FAINT, "ITEM");
                p.num(
                    area_x + (right - area_x) * 0.62,
                    y,
                    1.0 * k,
                    theme::FAINT,
                    "KIND",
                );
                p.num_right(right - 14.0 * k, y, 1.0 * k, theme::FAINT, "CREDITS");
            }
        }

        for (target, rect) in hits {
            let hot = self.hovered(rect);
            match target {
                Target::AccountRefresh => button(p, rect, k, "REFRESH", false, hot),
                Target::SignOut => button(p, rect, k, "SIGN OUT", false, hot),
                Target::BuyKey => {
                    let price = self.wallet.as_ref().map_or(0, |w| w.key_price);
                    button(p, rect, k, &format!("BUY A KEY  /  {price}"), false, hot);
                }
                Target::Item(i) => {
                    let Some(item) = self.items.get(*i) else {
                        continue;
                    };
                    let a = self.arrive(*i);
                    card(p, rect, 10.0 * k, item.equipped, hot, a);
                    p.text(
                        rect.x + 12.0 * k,
                        rect.y + 10.0 * k,
                        1.35 * k,
                        theme::faded(
                            if item.equipped {
                                theme::TEXT
                            } else {
                                theme::TEXT_DIM
                            },
                            a,
                        ),
                        &item.name.to_uppercase(),
                    );
                    p.num(
                        rect.x + 12.0 * k,
                        rect.y + 32.0 * k,
                        1.0 * k,
                        theme::faded(theme::FAINT, a * 1.6),
                        &format!("{}  /  {:.3}", wear_label(item.wear), item.wear),
                    );
                    p.num(
                        rect.x + 12.0 * k,
                        rect.y + 50.0 * k,
                        1.0 * k,
                        theme::faded(theme::FAINT, a * 1.6),
                        &item.weapon_id.to_uppercase(),
                    );
                    if item.equipped {
                        p.num_right(
                            rect.x + rect.w - 10.0 * k,
                            rect.y + 50.0 * k,
                            1.0 * k,
                            theme::faded(theme::AMBER, a),
                            "EQUIPPED",
                        );
                    }
                }
                Target::Case(i) => {
                    let Some(case) = self.wallet.as_ref().and_then(|w| w.cases.get(*i)) else {
                        continue;
                    };
                    p.rect(
                        rect.x,
                        rect.y,
                        rect.w,
                        rect.h,
                        [1.0, 1.0, 1.0, if hot { 0.07 } else { 0.035 }],
                    );
                    p.text(
                        rect.x + 10.0 * k,
                        rect.y + 9.0 * k,
                        1.2 * k,
                        theme::TEXT_DIM,
                        &case.name.to_uppercase(),
                    );
                    p.num_right(
                        rect.x + rect.w - 10.0 * k,
                        rect.y + 10.0 * k,
                        1.0 * k,
                        if hot { theme::AMBER } else { theme::FAINT },
                        "OPEN",
                    );
                }
                Target::Listing(i) => {
                    let Some(listing) = self.listings.get(*i) else {
                        continue;
                    };
                    let active = *i == self.listing;
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
                    p.text(
                        rect.x + 14.0 * k,
                        y,
                        1.4 * k,
                        ink,
                        &listing.name.to_uppercase(),
                    );
                    p.num(
                        rect.x + rect.w * 0.62,
                        y + 1.0 * k,
                        1.1 * k,
                        theme::faded(theme::FAINT, a * 1.5),
                        &listing.kind.to_uppercase(),
                    );
                    p.num_right(
                        rect.x + rect.w - 14.0 * k,
                        y,
                        1.4 * k,
                        theme::faded(theme::AMBER, a),
                        &listing.price.to_string(),
                    );
                }
                Target::Go => {
                    let (ready, sub) = match self.listings.get(self.listing) {
                        Some(listing) => (
                            true,
                            format!(
                                "{}  /  {} CREDITS",
                                listing.name.to_uppercase(),
                                listing.price
                            ),
                        ),
                        None => (false, "NOTHING SELECTED".to_string()),
                    };
                    go_button(p, rect, k, hot, ready, "BUY", &sub);
                }
                _ => {}
            }
        }
    }

    /// The sign-in panel: what an account is for, and the way in.
    fn paint_signin(&self, p: &mut Painter, hits: &[(Target, Hit)], k: f32, top: f32) {
        let left = MARGIN * k;
        heading(p, left, top, k, "ACCOUNT");
        p.text(left, top + 30.0 * k, 2.4 * k, theme::TEXT, "SIGN IN");
        for (i, line) in [
            "AN ACCOUNT KEEPS YOUR SKINS, CREDITS, CASES AND RATING.",
            "YOU CAN PLAY WITHOUT ONE, AS A GUEST.",
            "SIGNING IN HAPPENS IN YOUR BROWSER. NO PASSWORD IS TYPED HERE.",
        ]
        .iter()
        .enumerate()
        {
            p.num(
                left,
                top + (70.0 + 18.0 * i as f32) * k,
                1.05 * k,
                theme::TEXT_DIM,
                line,
            );
        }
        match &self.signin {
            SignIn::Starting(i) => {
                p.num(
                    left,
                    top + 160.0 * k,
                    1.2 * k,
                    theme::AMBER,
                    &format!("ASKING THE SERVER TO START {} SIGN-IN", PROVIDERS[*i].1),
                );
            }
            SignIn::Waiting {
                provider,
                url,
                code,
            } => {
                p.text(
                    left,
                    top + 140.0 * k,
                    1.5 * k,
                    theme::AMBER,
                    &format!("FINISH {} SIGN-IN IN YOUR BROWSER", PROVIDERS[*provider].1),
                );
                p.num(
                    left,
                    top + 166.0 * k,
                    1.0 * k,
                    theme::FAINT,
                    "IF IT DID NOT OPEN, GO TO",
                );
                // The address as it is: case matters in a link.
                p.name(left, top + 182.0 * k, 1.15 * k, theme::TEXT, url);
                if let Some(code) = code {
                    p.num(
                        left + (COLUMN + 32.0) * k,
                        top + 140.0 * k,
                        1.0 * k,
                        theme::FAINT,
                        "AND ENTER THIS CODE",
                    );
                    p.num(
                        left + (COLUMN + 32.0) * k,
                        top + 158.0 * k,
                        3.0 * k,
                        theme::AMBER,
                        code,
                    );
                }
            }
            SignIn::Failed(why) => {
                p.num(
                    left,
                    top + 126.0 * k,
                    1.1 * k,
                    theme::RED,
                    &why.to_uppercase(),
                );
            }
            SignIn::Idle => {}
        }
        for (target, rect) in hits {
            let hot = self.hovered(rect);
            match target {
                Target::Provider(i) => {
                    let on = self.providers.get(*i).copied().unwrap_or(false);
                    button(
                        p,
                        rect,
                        k,
                        &format!("SIGN IN WITH {}", PROVIDERS[*i].1),
                        on,
                        hot && on,
                    );
                    if !on {
                        p.num_right(
                            rect.x + rect.w - 10.0 * k,
                            rect.y + rect.h - 14.0 * k,
                            0.9 * k,
                            theme::FAINT,
                            "NOT SET UP HERE",
                        );
                    }
                }
                Target::SignInCancel => button(p, rect, k, "CANCEL", false, hot),
                _ => {}
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn front() -> Front {
        let mut f = Front::new("games.example", "Operative-7");
        f.maps = vec![MapEntry {
            name: "hd_dust2".into(),
            size: 1,
        }];
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

    fn signed_in(f: &mut Front) {
        f.account = Some(AccountView {
            name: "Sable".into(),
            handle: Some("sable".into()),
        });
    }

    #[test]
    fn a_guest_is_offered_sign_in_on_both_tabs_and_only_what_the_server_has() {
        let mut f = front();
        f.providers = [true, false];
        for tab in [Tab::Inventory, Tab::Store] {
            assert_eq!(
                click(&mut f, Target::Tab(tab)),
                None,
                "a guest has nothing to fetch"
            );
            let targets: Vec<Target> = f
                .hits(None, 1280.0, 720.0)
                .into_iter()
                .map(|h| h.0)
                .collect();
            assert!(
                targets.contains(&Target::Provider(0)) && targets.contains(&Target::Provider(1))
            );
            assert!(!targets
                .iter()
                .any(|t| matches!(t, Target::Item(_) | Target::BuyKey)));
        }
        // A provider this server has not set up says so and starts nothing.
        assert_eq!(click(&mut f, Target::Provider(1)), None);
        assert!(f
            .notice
            .as_ref()
            .is_some_and(|(why, _)| why.contains("GOOGLE")));
        assert_eq!(f.signin, SignIn::Idle);
        assert_eq!(click(&mut f, Target::Provider(0)), Some(Intent::SignIn(0)));
        assert_eq!(f.signin, SignIn::Starting(0));
        // While it runs there is one thing to press: stop.
        f.signin = SignIn::Waiting {
            provider: 0,
            url: "https://github.com/login/device".into(),
            code: Some("AB12-CD34".into()),
        };
        assert_eq!(
            click(&mut f, Target::SignInCancel),
            Some(Intent::SignInCancel)
        );
        assert_eq!(f.signin, SignIn::Idle);
    }

    #[test]
    fn opening_an_account_tab_signed_in_asks_for_what_it_shows() {
        let mut f = front();
        signed_in(&mut f);
        assert_eq!(
            click(&mut f, Target::Tab(Tab::Inventory)),
            Some(Intent::AccountRefresh)
        );
        assert_eq!(
            click(&mut f, Target::Tab(Tab::Store)),
            Some(Intent::AccountRefresh)
        );
        assert_eq!(click(&mut f, Target::Tab(Tab::Play)), None);
    }

    #[test]
    fn a_skin_is_equipped_or_taken_off_by_clicking_it() {
        let mut f = front();
        signed_in(&mut f);
        f.items = vec![
            ItemView {
                instance_id: "i1".into(),
                name: "Ember".into(),
                weapon_id: "assault".into(),
                wear: 0.02,
                equipped: false,
            },
            ItemView {
                instance_id: "i2".into(),
                name: "Fade".into(),
                weapon_id: "knife".into(),
                wear: 0.5,
                equipped: true,
            },
        ];
        f.tab = Tab::Inventory;
        assert_eq!(
            click(&mut f, Target::Item(0)),
            Some(Intent::Equip("i1".into()))
        );
        assert_eq!(
            click(&mut f, Target::Item(1)),
            Some(Intent::Unequip("knife".into()))
        );
        assert_eq!(click(&mut f, Target::SignOut), Some(Intent::SignOut));
        f.signed_out();
        assert!(f.account.is_none() && f.items.is_empty());
    }

    #[test]
    fn the_store_buys_what_can_be_paid_for_and_says_when_it_cannot() {
        let mut f = front();
        signed_in(&mut f);
        f.tab = Tab::Store;
        f.wallet = Some(WalletView {
            balance: 300,
            keys: 0,
            key_price: 250,
            cases: vec![CaseView {
                instance_id: "c1".into(),
                name: "Fever Case".into(),
            }],
        });
        f.listings = vec![
            ListingView {
                id: "l1".into(),
                name: "Ember".into(),
                kind: "skin".into(),
                price: 120,
            },
            ListingView {
                id: "l2".into(),
                name: "Fade".into(),
                kind: "skin".into(),
                price: 900,
            },
        ];
        assert_eq!(click(&mut f, Target::BuyKey), Some(Intent::BuyKey));
        // A case with no key explains itself instead of failing on the server.
        assert_eq!(click(&mut f, Target::Case(0)), None);
        assert!(f
            .notice
            .as_ref()
            .is_some_and(|(why, _)| why.contains("KEY")));
        f.wallet.as_mut().unwrap().keys = 1;
        assert_eq!(
            click(&mut f, Target::Case(0)),
            Some(Intent::OpenCase("c1".into()))
        );
        // Selecting is one click; buying is the button.
        assert_eq!(click(&mut f, Target::Listing(0)), None);
        assert_eq!(
            click(&mut f, Target::Go),
            Some(Intent::BuyListing("l1".into()))
        );
        assert_eq!(click(&mut f, Target::Listing(1)), None);
        assert_eq!(click(&mut f, Target::Go), None);
        assert!(f
            .notice
            .as_ref()
            .is_some_and(|(why, _)| why.contains("900")));
    }

    #[test]
    fn wear_reads_as_a_grade() {
        assert_eq!(wear_label(0.0), "FACTORY NEW");
        assert_eq!(wear_label(0.1), "MINIMAL WEAR");
        assert_eq!(wear_label(0.2), "FIELD TESTED");
        assert_eq!(wear_label(0.4), "WELL WORN");
        assert_eq!(wear_label(0.9), "BATTLE SCARRED");
    }

    #[test]
    fn every_account_screen_draws_inside_the_window_without_overlaps() {
        let mut f = front();
        f.providers = [true, true];
        let mut states: Vec<Box<dyn Fn(&mut Front)>> = vec![
            Box::new(|_| {}),
            Box::new(|f| {
                f.signin = SignIn::Waiting {
                    provider: 1,
                    url: "https://www.google.com/device".into(),
                    code: Some("ABCD-EFGH".into()),
                }
            }),
            Box::new(|f| f.signin = SignIn::Failed("the server did not answer".into())),
        ];
        states.push(Box::new(|f| {
            f.signin = SignIn::Idle;
            signed_in(f);
            f.items = (0..14)
                .map(|n| ItemView {
                    instance_id: format!("i{n}"),
                    name: format!("Skin {n}"),
                    weapon_id: "assault".into(),
                    wear: n as f32 / 14.0,
                    equipped: n == 0,
                })
                .collect();
            f.wallet = Some(WalletView {
                balance: 1250,
                keys: 2,
                key_price: 250,
                cases: (0..7)
                    .map(|n| CaseView {
                        instance_id: format!("c{n}"),
                        name: "Fever Case".into(),
                    })
                    .collect(),
            });
            f.listings = (0..20)
                .map(|n| ListingView {
                    id: format!("l{n}"),
                    name: format!("Listing {n}"),
                    kind: "skin".into(),
                    price: 100 + n,
                })
                .collect();
        }));
        for set in states {
            set(&mut f);
            for tab in [Tab::Inventory, Tab::Store] {
                f.tab = tab;
                for (w, h) in [(1280.0, 720.0), (1920.0, 1080.0)] {
                    let hits = f.hits(None, w, h);
                    for (i, (a, ra)) in hits.iter().enumerate() {
                        assert!(
                            ra.x >= 0.0
                                && ra.y >= 0.0
                                && ra.x + ra.w <= w + 0.5
                                && ra.y + ra.h <= h + 0.5,
                            "{a:?} leaves a {w}x{h} window"
                        );
                        for (b, rb) in hits.iter().skip(i + 1) {
                            let apart = ra.x + ra.w <= rb.x + 0.5
                                || rb.x + rb.w <= ra.x + 0.5
                                || ra.y + ra.h <= rb.y + 0.5
                                || rb.y + rb.h <= ra.y + 0.5;
                            assert!(apart, "{a:?} and {b:?} overlap at {w}x{h} on {tab:?}");
                        }
                    }
                    let mut out = Vec::new();
                    f.paint(None, w, h, &mut out);
                    assert!(out
                        .iter()
                        .all(|v| v.position[0].abs() <= 1.02 && v.position[1].abs() <= 1.02));
                }
            }
        }
    }

    #[test]
    fn every_account_label_is_in_the_fallback_font() {
        let mut labels: Vec<String> = PROVIDERS.iter().map(|p| p.1.to_string()).collect();
        labels.extend(
            [
                "SIGN IN WITH",
                "FINISH SIGN-IN IN YOUR BROWSER",
                "AN ACCOUNT KEEPS YOUR SKINS, CREDITS, CASES AND RATING.",
                "SIGNING IN HAPPENS IN YOUR BROWSER. NO PASSWORD IS TYPED HERE.",
                "NOTHING YET - SKINS COME FROM LEVEL DROPS, CASES AND THE MARKET",
                "BUY A KEY  /  250",
                "INVENTORY  /  3 SKINS",
            ]
            .map(String::from),
        );
        for wear in [0.0, 0.1, 0.2, 0.4, 0.9] {
            labels.push(wear_label(wear).to_string());
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
}

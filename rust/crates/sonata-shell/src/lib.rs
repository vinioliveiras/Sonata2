//! What the shell's surfaces share (sonata2/shell/): gtk4-layer-shell, the
//! login intro, the wallpaper of the moment, and the look of the login,
//! lock and welcome screens, with what those two share: Switch User, the
//! video wallpaper under the blur, the screen corners and the compositor's
//! idle watch.

pub mod authsheet;
pub mod idlewatch;
pub mod intro;
pub mod layer;
pub mod logins;
pub mod pam;
pub mod polkitagent;
pub mod screencorners;
pub mod throttle;
pub mod userswitch;
pub mod lockdisplay;
pub mod lockvideo;
pub mod loginui;
pub mod wallpaper;

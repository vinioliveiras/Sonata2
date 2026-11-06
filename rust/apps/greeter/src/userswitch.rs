//! The login screen's part of Switch User: a user who already has a
//! session on this seat gets it back -- the password asked once here, then
//! logind unlocks it and brings it to the front -- never a second session.
//! The logic (sonata2/userswitch.py) is shared with the lock screen, which
//! shows this login screen: sonata_shell::userswitch.

pub use sonata_shell::userswitch::*;

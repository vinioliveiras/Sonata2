//! sonata2-rs-assistant: the Assistant on its own (`sonata2-rs assistant`
//! starts it), so only it needs libsoup and libsecret.

fn main() -> std::process::ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    std::process::ExitCode::from(sonata_assistant::run(&args).value() as u8)
}

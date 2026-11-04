//! sonata2-rs-terminal: Terminal on its own (`sonata2-rs terminal` starts
//! it), so only it needs libvte -- the shell's other parts start without.

fn main() -> std::process::ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    std::process::ExitCode::from(sonata_terminal::run(&args).value() as u8)
}

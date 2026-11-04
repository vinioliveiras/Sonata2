//! sonata2-rs-videos: Videos on its own (`sonata2-rs videos` starts it),
//! so only it needs GStreamer -- the shell's other parts start without.

fn main() -> std::process::ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    std::process::ExitCode::from(sonata_videos::run(&args).value() as u8)
}

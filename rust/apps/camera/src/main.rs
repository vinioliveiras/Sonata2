//! sonata2-rs-camera: Camera on its own (`sonata2-rs camera` starts it),
//! so only it needs GStreamer -- the shell's other parts start without.

fn main() -> std::process::ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    std::process::ExitCode::from(sonata_camera::run(&args).value() as u8)
}

//! Standalone Clone Maker CLI; game patching lives in the library.
use mssb_clone_core::cli;
fn main() {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    let command = match cli::parse(&args) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("{}", cli::usage_error(&e));
            std::process::exit(2);
        }
    };
    if let Err(e) = cli::execute(command, &mut |s| println!("{s}")) {
        eprintln!("{e}");
        std::process::exit(1);
    }
}

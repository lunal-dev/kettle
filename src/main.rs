use clap::{
    Parser, Subcommand,
    builder::{Styles, styling::AnsiColor},
};
use colored::Colorize;

mod amd;
mod commands;
mod hcl;
mod provenance;

const STYLES: Styles = Styles::styled()
    .header(AnsiColor::Yellow.on_default())
    .usage(AnsiColor::Green.on_default())
    .literal(AnsiColor::Green.on_default())
    .placeholder(AnsiColor::Green.on_default());

/// Kettle creates and validates cryptographically secure software builds.
///
/// Use Kettle-attested builds to know the exact inputs to any build, and to be confident your
/// build process was not seen or interfered with by any third parties, thanks to attestations
/// provided by the Trusted Execution Environment where the build was run.
#[derive(Parser, Debug)]
#[command(version, styles=STYLES)]
struct Args {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Build a project inside a Trusted Execution Environment
    Build {
        /// Path to the Cargo or Nix project
        #[arg(default_value = ".")]
        path: String,
    },
    /// Verify a Kettle build, including provenance and attestation
    Verify {
        /// Path to directory containing provenance.json and evidence.b64
        #[arg(default_value = ".")]
        path: String,
    },
}

fn main() {
    let result = match Args::parse().command {
        Commands::Build { path } => commands::build::build(path),
        Commands::Verify { path } => commands::verify::verify(path),
    };

    if let Err(e) = result {
        eprintln!("{}", "Error during run:".red());
        eprintln!("  {}", e);
        std::process::exit(1);
    }
}

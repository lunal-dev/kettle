use clap::{
    Parser, Subcommand,
    builder::{Styles, styling::AnsiColor},
};
use colored::Colorize;
use std::path::PathBuf;

use kettle::commands;

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
    #[arg(long, help = "Enable verbose output", global = true)]
    verbose: bool,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Build and attest a project inside a Trusted Execution Environment
    #[cfg(all(feature = "attest", target_os = "linux"))]
    Attest {
        #[arg()]
        path: PathBuf,
    },
    #[cfg(not(all(feature = "attest", target_os = "linux")))]
    #[command(hide = true)]
    Attest {
        #[arg(default_value = ".")]
        path: PathBuf,
    },
    /// Build a project with SLSA v1.2 provenance
    Build {
        /// Path to the Cargo or Nix project
        #[arg()]
        path: PathBuf,
    },
    /// Verify a Kettle build, including provenance and attestation
    Verify {
        /// Path to directory containing provenance.json and evidence.b64
        #[arg(default_value = ".")]
        path: PathBuf,
        #[arg(long, help = "Verbose output, including the entire attestation report")]
        verbose: bool,
    },
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args = Args::parse();
    let result = match args.command {
        Commands::Attest { ref path } => commands::attest::attest(path).await,
        Commands::Build { ref path } => commands::build::build(path),
        Commands::Verify { ref path, verbose } => commands::verify::verify(path, verbose).await,
    };

    if args.verbose {
        result
    } else {
        if let Err(e) = result {
            eprintln!("{}", "Error during run:".red());
            eprintln!("  {}", e);
            std::process::exit(1);
        }

        Ok(())
    }
}

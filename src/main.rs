use clap::{Parser, Subcommand};

/// Kettle creates and validates cryptographically secure software builds.
///
/// Use Kettle-attested builds to know the exact inputs to any build, and to be confident your
/// build process was not seen or interfered with by any third parties, thanks to attestations
/// provided by the Trusted Execution Environment where the build was run.
#[derive(Parser, Debug)]
#[command(version)]
struct Args {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand, Debug)]
enum Commands {
    /// Build a project inside a Trusted Execution Environment
    Build {
        /// Path to the build manifest
        #[arg(short, long)]
        manifest: Option<String>,
    },
    /// Verify a Kettle build, including provenance and attestation
    Verify {
        /// Path to the provenance file to verify
        #[arg(short, long)]
        provenance: Option<String>,
    },
}

fn main() {
    let args = Args::parse();

    match args.command {
        Commands::Build { manifest } => {
            println!("Building with manifest: {:?}", manifest);
        }
        Commands::Verify { provenance } => {
            println!("Verifying provenance: {:?}", provenance);
        }
    }
}

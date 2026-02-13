"""
SentinelAI Setup Script
========================
This script sets up all the required dependencies for running SentinelAI.
It downloads and configures:
- Python dependencies
- Piper TTS (text-to-speech)
- Vosk models (speech-to-text)
- LLaMA GGUF model (LLM)
- Poppler (PDF processing)

Usage:
    python setup.py
"""

import os
import sys
import subprocess
import zipfile
import tarfile
import shutil
import urllib.request
from pathlib import Path

# Base directory (where this script is located)
BASE_DIR = Path(__file__).parent.resolve()
RESOURCES_DIR = BASE_DIR / "resources"

# ================== CONFIGURATION ==================

# Piper TTS Configuration
PIPER_VERSION = "2023.11.14-2"
PIPER_URL = f"https://github.com/rhasspy/piper/releases/download/{PIPER_VERSION}/piper_windows_amd64.zip"
PIPER_DIR = RESOURCES_DIR / "piper_models"

# Vosk Model Configuration
VOSK_MODEL_NAME = "vosk-model-en-in-0.5"
VOSK_MODEL_URL = f"https://alphacephei.com/vosk/models/{VOSK_MODEL_NAME}.zip"
VOSK_DIR = RESOURCES_DIR / "vosk_models"

# LLaMA Model Configuration
LLAMA_MODEL_NAME = "Llama-3.2-3B-Instruct-Q4_K_M.gguf"
LLAMA_MODEL_URL = f"https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF/resolve/main/{LLAMA_MODEL_NAME}"
MODELS_DIR = RESOURCES_DIR / "models"

# Poppler Configuration (for PDF processing)
POPPLER_VERSION = "24.08.0-0"
POPPLER_URL = f"https://github.com/oschwartz10612/poppler-windows/releases/download/v{POPPLER_VERSION}/Release-{POPPLER_VERSION}.zip"
POPPLER_DIR = RESOURCES_DIR / "poppler"

# Piper Repository (for training/custom voices)
PIPER_REPO_URL = "https://github.com/rhasspy/piper.git"
PIPER_REPO_DIR = RESOURCES_DIR / "piper"
PIPER_PATCHES_DIR = RESOURCES_DIR / "piper_patches"

# ================== HELPER FUNCTIONS ==================

def print_header(message):
    """Print a formatted header."""
    print("\n" + "=" * 60)
    print(f"  {message}")
    print("=" * 60)


def print_step(message):
    """Print a step message."""
    print(f"\n[*] {message}")


def print_success(message):
    """Print a success message."""
    print(f"[✓] {message}")


def print_error(message):
    """Print an error message."""
    print(f"[✗] {message}")


def print_warning(message):
    """Print a warning message."""
    print(f"[!] {message}")


def download_file(url, dest_path, desc="file"):
    """Download a file with progress indication."""
    print_step(f"Downloading {desc}...")
    print(f"    URL: {url}")
    print(f"    Destination: {dest_path}")
    
    try:
        def reporthook(block_num, block_size, total_size):
            downloaded = block_num * block_size
            if total_size > 0:
                percent = min(100, downloaded * 100 / total_size)
                downloaded_mb = downloaded / (1024 * 1024)
                total_mb = total_size / (1024 * 1024)
                sys.stdout.write(f"\r    Progress: {percent:.1f}% ({downloaded_mb:.1f}/{total_mb:.1f} MB)")
                sys.stdout.flush()
        
        urllib.request.urlretrieve(url, dest_path, reporthook)
        print()  # New line after progress
        print_success(f"Downloaded {desc}")
        return True
    except Exception as e:
        print_error(f"Failed to download {desc}: {e}")
        return False


def extract_zip(zip_path, dest_dir):
    """Extract a ZIP file."""
    print_step(f"Extracting {zip_path.name}...")
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(dest_dir)
        print_success(f"Extracted to {dest_dir}")
        return True
    except Exception as e:
        print_error(f"Failed to extract: {e}")
        return False


def run_command(command, desc="command", cwd=None):
    """Run a shell command."""
    print_step(f"Running: {desc}")
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            print_success(f"Completed: {desc}")
            return True
        else:
            print_error(f"Failed: {desc}")
            if result.stderr:
                print(f"    Error: {result.stderr[:500]}")
            return False
    except Exception as e:
        print_error(f"Exception running {desc}: {e}")
        return False


# ================== SETUP FUNCTIONS ==================

def install_python_dependencies():
    """Install Python dependencies from requirements.txt."""
    print_header("Installing Python Dependencies")
    
    req_file = BASE_DIR / "requirements.txt"
    if not req_file.exists():
        print_error("requirements.txt not found!")
        return False
    
    return run_command(
        f'"{sys.executable}" -m pip install -r "{req_file}"',
        "pip install requirements.txt"
    )


def setup_piper():
    """Download and setup Piper TTS."""
    print_header("Setting up Piper TTS")
    
    PIPER_DIR.mkdir(exist_ok=True)
    piper_exe = PIPER_DIR / "piper.exe"
    
    if piper_exe.exists():
        print_success("Piper already installed, skipping...")
        return True
    
    # Download Piper
    zip_path = PIPER_DIR / "piper.zip"
    if not download_file(PIPER_URL, zip_path, "Piper TTS"):
        return False
    
    # Extract
    if not extract_zip(zip_path, PIPER_DIR):
        return False
    
    # Move files from nested piper folder if exists
    nested_piper = PIPER_DIR / "piper"
    if nested_piper.exists() and nested_piper.is_dir():
        for item in nested_piper.iterdir():
            shutil.move(str(item), str(PIPER_DIR / item.name))
        nested_piper.rmdir()
    
    # Clean up zip
    zip_path.unlink(missing_ok=True)
    
    # Create models directory for custom ONNX models
    (PIPER_DIR / "models").mkdir(exist_ok=True)
    
    print_success("Piper TTS setup complete!")
    print_warning("Note: You need to add your custom ONNX voice model to piper_models/models/")
    return True


def setup_vosk():
    """Download and setup Vosk speech recognition model."""
    print_header("Setting up Vosk Speech Recognition")
    
    VOSK_DIR.mkdir(exist_ok=True)
    model_dir = VOSK_DIR / VOSK_MODEL_NAME
    
    if model_dir.exists():
        print_success("Vosk model already installed, skipping...")
        return True
    
    # Download model
    zip_path = VOSK_DIR / f"{VOSK_MODEL_NAME}.zip"
    if not download_file(VOSK_MODEL_URL, zip_path, "Vosk model"):
        return False
    
    # Extract
    if not extract_zip(zip_path, VOSK_DIR):
        return False
    
    # Clean up zip
    zip_path.unlink(missing_ok=True)
    
    print_success("Vosk setup complete!")
    return True


def setup_llama_model():
    """Download LLaMA GGUF model."""
    print_header("Setting up LLaMA Model")
    
    MODELS_DIR.mkdir(exist_ok=True)
    model_path = MODELS_DIR / LLAMA_MODEL_NAME
    
    if model_path.exists():
        print_success("LLaMA model already installed, skipping...")
        return True
    
    print_warning("This is a large download (~2GB). Please be patient...")
    
    if not download_file(LLAMA_MODEL_URL, model_path, "LLaMA GGUF model"):
        print_warning("Failed to download LLaMA model.")
        print_warning("You can manually download from:")
        print(f"    https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF")
        print(f"    Save as: {model_path}")
        return False
    
    print_success("LLaMA model setup complete!")
    return True


def setup_poppler():
    """Download and setup Poppler for PDF processing."""
    print_header("Setting up Poppler (PDF Processing)")
    
    # Check if poppler already exists (any version)
    existing_poppler = list(BASE_DIR.glob("poppler*"))
    if existing_poppler:
        print_success(f"Poppler already installed at {existing_poppler[0]}, skipping...")
        return True
    
    POPPLER_DIR.mkdir(exist_ok=True)
    
    # Download Poppler
    zip_path = POPPLER_DIR / "poppler.zip"
    if not download_file(POPPLER_URL, zip_path, "Poppler"):
        print_warning("Failed to download Poppler. Manual installation required.")
        print_warning("Download from: https://github.com/oschwartz10612/poppler-windows/releases")
        return False
    
    # Extract
    if not extract_zip(zip_path, POPPLER_DIR):
        return False
    
    # Clean up zip
    zip_path.unlink(missing_ok=True)
    
    # Find the bin directory and add to PATH instructions
    bin_dirs = list(POPPLER_DIR.rglob("bin"))
    if bin_dirs:
        print_success("Poppler setup complete!")
        print_warning(f"Add to PATH: {bin_dirs[0]}")
    
    return True


def clone_piper_repo():
    """Clone Piper repository and apply custom patches."""
    print_header("Cloning Piper Repository")
    
    if PIPER_REPO_DIR.exists():
        print_success("Piper repository already cloned, skipping clone...")
        # Still apply patches in case they were updated
        return apply_piper_patches()
    
    print_step("Cloning Piper repository from GitHub...")
    
    success = run_command(
        f'git clone "{PIPER_REPO_URL}" "{PIPER_REPO_DIR}"',
        "Cloning Piper repository"
    )
    
    if success:
        return apply_piper_patches()
    return False


def apply_piper_patches():
    """Apply custom patches to the cloned Piper repository."""
    print_step("Applying custom Piper patches...")
    
    patches_dir = PIPER_PATCHES_DIR
    if not patches_dir.exists():
        print_warning("No piper_patches folder found, skipping patches...")
        return True
    
    # Copy all files from piper_patches to piper, preserving directory structure
    try:
        for src_file in patches_dir.rglob("*"):
            if src_file.is_file():
                # Calculate relative path from piper_patches
                rel_path = src_file.relative_to(patches_dir)
                dest_file = PIPER_REPO_DIR / rel_path
                
                # Create parent directories if needed
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                
                # Copy the file
                shutil.copy2(src_file, dest_file)
                print_success(f"Patched: {rel_path}")
        
        print_success("All Piper patches applied successfully!")
        return True
    except Exception as e:
        print_error(f"Failed to apply patches: {e}")
        return False


def create_directories():
    """Create required directories."""
    print_header("Creating Required Directories")
    
    directories = [
        BASE_DIR / "models",
        BASE_DIR / "piper_models",
        BASE_DIR / "piper_models" / "models",
        BASE_DIR / "vosk_models",
        BASE_DIR / "faiss_index",
        BASE_DIR / "data",
    ]
    
    for directory in directories:
        directory.mkdir(exist_ok=True)
        print_success(f"Created: {directory.name}/")
    
    return True


def verify_setup():
    """Verify that all components are set up correctly."""
    print_header("Verifying Setup")
    
    checks = [
        ("Python dependencies", BASE_DIR / "requirements.txt"),
        ("Piper executable", PIPER_DIR / "piper.exe"),
        ("Piper repository", PIPER_REPO_DIR),
        ("Vosk model", VOSK_DIR / VOSK_MODEL_NAME),
        ("LLaMA model", MODELS_DIR / LLAMA_MODEL_NAME),
        ("Models directory", MODELS_DIR),
        ("FAISS index directory", BASE_DIR / "faiss_index"),
    ]
    
    all_good = True
    for name, path in checks:
        if path.exists():
            print_success(f"{name}: OK")
        else:
            print_warning(f"{name}: MISSING - {path}")
            all_good = False
    
    # Check for poppler with any version
    poppler_dirs = list(BASE_DIR.glob("poppler*"))
    if poppler_dirs:
        print_success(f"Poppler: OK ({poppler_dirs[0].name})")
    else:
        print_warning("Poppler: MISSING")
        all_good = False
    
    return all_good


def print_next_steps():
    """Print next steps after setup."""
    print_header("Setup Complete!")
    
    print("""
Next Steps:
-----------
1. Add your custom Piper ONNX voice model to:
   piper_models/models/<your_model>/

2. Update paths in rag_response.py if needed:
   - PIPER_MODEL: Path to your .onnx voice model
   - LLAMA_MODEL: Path to your GGUF model

3. Add documents to the 'data/' folder for indexing

4. Run embedding.py to create FAISS index:
   python embedding.py

5. Run the main application:
   python all_combined_testing.py

Environment Variables:
----------------------
Create a .env file with:
   HF_TOKEN=your_huggingface_token  (if needed)
""")


# ================== MAIN ==================

def main():
    """Main setup function."""
    print_header("SentinelAI Setup")
    print(f"Base Directory: {BASE_DIR}")
    
    # Create directories first
    create_directories()
    
    # Setup components
    steps = [
        ("Python Dependencies", install_python_dependencies),
        ("Piper TTS", setup_piper),
        ("Piper Repository", clone_piper_repo),
        ("Vosk Speech Recognition", setup_vosk),
        ("LLaMA Model", setup_llama_model),
        ("Poppler PDF Tools", setup_poppler),
    ]
    
    results = {}
    for name, func in steps:
        try:
            results[name] = func()
        except Exception as e:
            print_error(f"Error setting up {name}: {e}")
            results[name] = False
    
    # Verify setup
    verify_setup()
    
    # Print summary
    print_header("Setup Summary")
    for name, success in results.items():
        status = "✓ SUCCESS" if success else "✗ FAILED"
        print(f"  {name}: {status}")
    
    # Print next steps
    print_next_steps()


if __name__ == "__main__":
    main()

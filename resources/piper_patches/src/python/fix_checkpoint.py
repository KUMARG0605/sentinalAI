import pathlib
import platform
import torch
import sys

# Patch PosixPath for Windows
if platform.system() == 'Windows':
    temp = pathlib.PosixPath
    pathlib.PosixPath = pathlib.WindowsPath

# Function to convert all Path objects to strings recursively
def convert_paths_to_strings(obj):
    if isinstance(obj, (pathlib.Path, pathlib.PosixPath, pathlib.WindowsPath)):
        return str(obj)
    elif isinstance(obj, dict):
        return {k: convert_paths_to_strings(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_paths_to_strings(item) for item in obj]
    elif isinstance(obj, tuple):
        return tuple(convert_paths_to_strings(item) for item in obj)
    return obj

# Load and re-save checkpoint
checkpoint_path = sys.argv[1]
output_path = sys.argv[2] if len(sys.argv) > 2 else checkpoint_path.replace('.ckpt', '_fixed.ckpt')

print(f"Loading checkpoint from: {checkpoint_path}")
# Add WindowsPath to safe globals for loading
torch.serialization.add_safe_globals([pathlib.WindowsPath, pathlib.PosixPath])
checkpoint = torch.load(checkpoint_path, map_location='cpu', weights_only=False)

# Convert all Path objects to strings
print("Converting Path objects to strings...")
checkpoint = convert_paths_to_strings(checkpoint)

print(f"Saving fixed checkpoint to: {output_path}")
torch.save(checkpoint, output_path)
print("Done!")

# Restore original PosixPath
if platform.system() == 'Windows':
    pathlib.PosixPath = temp
